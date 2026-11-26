"""De volledige set exporteren: hele video met gecorrigeerde sync, en audio (MP3/WAV/FLAC) op een goed niveau
zonder clipping.

Niveau ('mastering') in drie stappen:
  1. Meten (EBU R128): geïntegreerde loudness (LUFS) en true peak (dBTP) van het gekozen stuk, plus een controle of
     de opname zelf al afgetopte pieken (clipping) bevat.
  2. Eén vaste gain voor het hele stuk (dynamiek blijft zoals de DJ hem mixte). Komen de pieken daardoor boven het
     plafond, dan vangt een limiter op 4× samplefrequentie ze op (benadert true peak, ook tussen samples).
  3. Na het coderen wordt het resultaat opnieuw gemeten. Komt een MP3 door het coderen toch boven het plafond,
     dan wordt automatisch opnieuw gecodeerd met een iets lager plafond.
"""
from __future__ import annotations
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable

from .exporter import Cancelled, _fmt, audio_window, check_disk_space, reserve_unique, run_ffmpeg, safe_stem
from .ffmpeg_tools import SourceInfo, ToolError
from .platform_paths import no_window_kwargs
from .model import ExportSettings, Source, av_offset_at

AUDIO_FORMATS: dict[str, tuple[str, str]] = {
    "mp3_320": ("MP3 320 kbps (standaard, past overal)", ".mp3"),
    "mp3_v0": ("MP3 VBR V0 (± 245 kbps, kleiner, zelfde kwaliteit)", ".mp3"),
    "wav24": ("WAV 24-bit (ongecomprimeerd, voor verdere bewerking)", ".wav"),
    "flac": ("FLAC 24-bit (lossless, kleiner dan WAV)", ".flac"),
}
MASTER_PRESETS: list[tuple[str, float, str]] = [
    ("Streaming −14 LUFS (YouTube, Spotify, SoundCloud) – standaard", -14.0, "loudness"),
    ("Luid −9 LUFS (clubniveau, Mixcloud)", -9.0, "loudness"),
    ("Rustig −16 LUFS (podcast, Apple)", -16.0, "loudness"),
    ("Alleen pieken veilig (loudness ongewijzigd)", 0.0, "peak"),
    ("Niets aanpassen (exact de opname)", 0.0, "off"),
]
CLIP_WARNING = ("De opname zelf lijkt op de pieken afgetopt (clipping, of een keiharde limiter in mixer of recorder; "
                "pieken tot {tp:+.1f} dBTP). Dat is achteraf niet te herstellen; deze export voegt in elk geval geen "
                "nieuwe clipping toe. Tip voor de volgende opname: zet het opnameniveau een paar dB lager.")
VIDEO_COPY_CODECS = {"h264", "hevc", "av1", "vp9", "mpeg4"}


@dataclass
class Levels:
    integrated: float = -70.0      # LUFS
    true_peak: float = -70.0       # dBTP
    lra: float = 0.0
    clip_events: int = 0           # aantal keren dat de opname de maximale waarde raakte (bij ±0 dBFS)
    sample_peak: float = -70.0     # dBFS
    flat_windows: int = 0          # meetplekken met 'opgestapelde' pieken (zie clip_scan)
    scan_windows: int = 0          # aantal onderzochte plekken

    @property
    def source_clipped(self) -> bool:
        # Afgetopte pieken (veel keer exact de maximale waarde), een true peak duidelijk boven 0 dBTP (afgetopte
        # pieken in een MP3/AAC-bron komen na decoderen boven 0 uit), of opgestapelde pieken op meerdere plekken.
        return ((self.sample_peak > -0.1 and (self.clip_events >= 20 or self.true_peak > 0.5))
                or (self.scan_windows > 0 and self.flat_windows >= max(1, self.scan_windows // 4)))


@dataclass
class MasterResult:
    path: str
    before: Levels
    after: Levels
    gain_db: float
    limited_db: float = 0.0        # hoeveel de limiter (maximaal) moest wegdrukken, schatting
    passes: int = 1
    warnings: list = field(default_factory=list)

    def summary(self) -> str:
        s = (f"Voor: {self.before.integrated:.1f} LUFS, piek {self.before.true_peak:+.1f} dBTP · "
             f"Na: {self.after.integrated:.1f} LUFS, piek {self.after.true_peak:+.1f} dBTP · gain {self.gain_db:+.1f} dB")
        if self.limited_db > 0.05:
            s += f" · limiter ±{self.limited_db:.1f} dB"
        return s


# ----------------------------------------------------------------------------- bereik en sync
def full_range(info: SourceInfo, st: ExportSettings) -> tuple[float, float]:
    start = max(0.0, min(st.full_start, info.duration))
    end = st.full_end if st.full_end > 0 else info.duration
    end = min(end, info.duration)
    if end - start < 0.5:
        raise ToolError("Het gekozen stuk is te kort (controleer begin en einde).")
    return start, end - start


def _sync_active(info: SourceInfo, st: ExportSettings) -> bool:
    return info.has_audio and not info.audio_only and (abs(st.av_offset) >= 0.0005 or abs(st.av_drift) > 1e-9)


def audio_input(info: SourceInfo, st: ExportSettings, v_start: float) -> tuple[float, list[str]]:
    """Waar het geluid in de bron begint en welke filters de sync herstellen (incl. drift).
    Beeld op bron-tijd τ hoort bij geluid op s met s + offset(s) = τ  →  s = (τ − d0) / (1 + b)."""
    if not _sync_active(info, st):
        return v_start, []
    d0, b = float(st.av_offset), float(st.av_drift)
    s0 = (v_start - d0) / (1.0 + b)
    f: list[str] = []
    if abs(b) > 1e-9:
        f.append(f"atempo={1.0 / (1.0 + b):.9f}")       # geluid ietsje langzamer/sneller (toonhoogte blijft)
    a_start, pad = audio_window(s0, 0.0)
    if pad > 0:
        f.append(f"adelay=delays={pad * (1.0 + b) * 1000:.1f}:all=1")
    return a_start, f


# ----------------------------------------------------------------------------- meten
_NUM = r"(-?(?:\d+(?:\.\d+)?|inf))"


def parse_levels(lines: list[str]) -> Levels:
    text = "\n".join(lines)
    lv = Levels()
    summ = text[text.rfind("Summary:"):] if "Summary:" in text else text
    m = re.search(r"I:\s+" + _NUM + r" LUFS", summ)
    if m:
        lv.integrated = float(m.group(1)) if "inf" not in m.group(1) else -70.0
    m = re.search(r"LRA:\s+" + _NUM + r" LU", summ)
    if m and "inf" not in m.group(1):
        lv.lra = float(m.group(1))
    m = re.search(r"True peak:\s*\n\s*Peak:\s+" + _NUM + r" dBFS", summ)
    if m:
        lv.true_peak = float(m.group(1)) if "inf" not in m.group(1) else -70.0
    ov = text[text.rfind("Overall"):] if "Overall" in text else text
    m = re.search(r"Peak level dB:\s*" + _NUM, ov)
    if m and "inf" not in m.group(1):
        lv.sample_peak = float(m.group(1))
    m = re.search(r"Peak count:\s*(\d+)", ov)
    if m:
        lv.clip_events = int(m.group(1))
    return lv


def clip_scan(ffmpeg: str, path: str, start: float, duration: float, n: int = 12, length: float = 8.0,
              cancel: threading.Event | None = None) -> tuple[int, int]:
    """Telt op hoeveel van n plekken de pieken 'opgestapeld' zijn: veel samples binnen 0,5 dB van het maximum
    terwijl dat maximum dicht bij 0 dBFS ligt. Bij natuurlijke muziek is het hoogste stukje zeldzaam; bij een
    afgetopte (geclipte) of keihard begrensde opname ligt een groot deel van de pieken op dezelfde hoogte.
    Betrouwbaar bij WAV/PCM en licht gecomprimeerde bronnen; bij sterk gecomprimeerde MP3/AAC-opnames vervaagt het
    kenmerk en wordt clipping niet altijd herkend. Geeft (plekken met opgestapelde pieken, onderzochte plekken)."""
    import numpy as np
    hits = done = 0
    pos = [start + (duration - length) * (i + 0.5) / n for i in range(n)] if duration > length * 2 else [start]
    for t in pos:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        cp = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-t", f"{length:.3f}", "-i", path,
                             "-map", "0:a:0", "-vn", "-f", "f32le", "-acodec", "pcm_f32le", "-"],
                            capture_output=True, **no_window_kwargs())
        x = np.abs(np.frombuffer(cp.stdout[: len(cp.stdout) // 4 * 4], dtype=np.float32))
        if len(x) < 1000:
            continue
        done += 1
        m = float(x.max())
        if m < 0.7:                      # pieken onder −3 dBFS: niet afgetopt
            continue
        if float(np.mean(x >= m * 0.944)) > 0.002:       # > 0,2 % van de samples binnen 0,5 dB van het maximum
            hits += 1
    return hits, done


def measure(ffmpeg: str, path: str, start: float, duration: float, pre: list[str],
            progress: Callable[[float], None] | None = None, cancel: threading.Event | None = None,
            stream: str = "0:a:0") -> Levels:
    chain = pre + ["ebur128=peak=true:framelog=quiet", "astats=metadata=0:measure_perchannel=none"]
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-progress", "pipe:1", "-nostats", "-ss", f"{start:.6f}",
           "-t", f"{duration:.6f}", "-i", path, "-map", stream, "-vn", "-af", ",".join(chain), "-f", "null", "-"]
    lines = run_ffmpeg(cmd, duration, progress, cancel, keep_lines=400)
    lv = parse_levels(lines)
    if lv.integrated <= -69.9:
        raise ToolError("Dit stuk is (vrijwel) stil; het niveau kan niet worden gemeten.")
    return lv


def master_chain(st: ExportSettings, lv: Levels, rate: int, ceiling: float | None = None,
                 extra_gain: float = 0.0) -> tuple[list[str], float, float]:
    """(filters, gain_dB, geschatte limiterreductie_dB)."""
    ceiling = st.master_ceiling if ceiling is None else ceiling
    if st.master_mode == "off":
        return [], 0.0, 0.0
    if st.master_mode == "peak":
        gain = ceiling - lv.true_peak
    else:
        gain = st.master_lufs - lv.integrated + extra_gain
    f = []
    if abs(gain) > 0.01:
        f.append(f"volume={_fmt(round(gain, 2))}dB")
    over = lv.true_peak + gain - ceiling
    limited = 0.0
    if st.master_mode == "loudness" and over > -0.3:
        limited = max(0.0, over)
        lim = 10 ** ((ceiling - 0.2) / 20.0)
        # op 4× samplefrequentie limiteren: vangt ook pieken tussen de samples (true peak)
        f += [f"aresample={rate * 4}", f"alimiter=limit={lim:.5f}:attack=2:release=80:level=0",
              f"aresample={rate}"]
    return f, gain, limited


MAX_LIMIT_DB = 12.0     # meer wegdrukken klinkt niet meer als de mix; dan liever iets onder het doel blijven


def render_mastered(ffmpeg: str, make_cmd: Callable[[list[str]], list[str]], part: str, before: Levels,
                    st: ExportSettings, rate: int, dur: float, rep, lo: float, hi: float,
                    cancel: threading.Event | None, stream: str = "0:a:0") -> MasterResult:
    """Coderen → resultaat meten → zo nodig bijsturen (max. 3 keer):
    - true peak boven het plafond (kan door MP3/AAC-codering)  → plafond iets lager;
    - loudness onder het doel omdat de limiter energie wegnam → gain iets hoger (tot MAX_LIMIT_DB limiting)."""
    ceiling, extra = st.master_ceiling, 0.0
    res: MasterResult | None = None
    warnings: list[str] = []
    for attempt in range(3):
        chain, gain, limited = master_chain(st, before, rate, ceiling, extra)
        a, b = lo + (hi - lo) * attempt / 3, lo + (hi - lo) * (attempt + 1) / 3
        run_ffmpeg(make_cmd(chain), dur, rep(a, a + (b - a) * 0.75, "Exporteren…"), cancel)
        after = measure(ffmpeg, part, 0.0, dur + 1, [], rep(a + (b - a) * 0.75, b, "Resultaat controleren…"),
                        cancel, stream=stream)
        res = MasterResult(part, before, after, gain, limited, attempt + 1, warnings)
        if st.master_mode == "off":
            break
        peak_ok = after.true_peak <= st.master_ceiling + 0.1
        short = st.master_lufs - after.integrated if st.master_mode == "loudness" else 0.0
        can_push = limited + short <= MAX_LIMIT_DB
        if peak_ok and (short <= 0.5 or not can_push):
            break
        if not peak_ok:
            ceiling -= (after.true_peak - st.master_ceiling) + 0.1
        if short > 0.5 and can_push:
            extra += short
    assert res is not None
    if st.master_mode != "off" and res.after.true_peak > st.master_ceiling + 0.1:
        warnings.append(f"Het plafond van {st.master_ceiling:g} dBTP is net niet gehaald "
                        f"({res.after.true_peak:+.1f} dBTP)"
                        + (", maar er is geen digitale clipping." if res.after.true_peak < 0 else "."))
    if st.master_mode == "loudness" and st.master_lufs - res.after.integrated > 0.5:
        warnings.append(f"Het doel van {st.master_lufs:g} LUFS is niet helemaal gehaald ({res.after.integrated:.1f} LUFS): "
                        f"daarvoor zou de limiter meer dan {MAX_LIMIT_DB:g} dB moeten wegdrukken en dat klinkt niet "
                        "meer als je mix.")
    elif res.limited_db > 6:
        warnings.append(f"Voor dit doel drukt de limiter tot ±{res.limited_db:.0f} dB weg; de kicks kunnen daardoor "
                        "iets platter klinken. Kies −14 LUFS voor een natuurlijker geluid.")
    return res


# ----------------------------------------------------------------------------- audio-export
def _audio_codec_args(fmt: str) -> list[str]:
    if fmt == "mp3_320":
        return ["-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3"]
    if fmt == "mp3_v0":
        return ["-c:a", "libmp3lame", "-q:a", "0", "-id3v2_version", "3"]
    if fmt == "wav24":
        return ["-c:a", "pcm_s24le"]
    if fmt == "flac":
        return ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"]
    raise ToolError(f"Onbekend audioformaat: {fmt}")


def _rate_for(fmt: str, info: SourceInfo) -> int:
    if fmt.startswith("mp3"):
        return 44100
    return info.audio_rate if info.audio_rate in (44100, 48000, 88200, 96000) else 48000


def _metadata(source: Source, st: ExportSettings) -> list[str]:
    title = source.title or os.path.splitext(source.name)[0]
    md = ["-metadata", f"title={title}", "-metadata", "album=Radio ACCU"]
    if st.dj_name.strip():
        md += ["-metadata", f"artist={st.dj_name.strip()}"]
    if st.master_mode == "loudness":
        md += ["-metadata", f"comment=Radio ACCU Clipper: {st.master_lufs:g} LUFS, max {st.master_ceiling:g} dBTP"]
    return md


def _out_name(source: Source, st: ExportSettings, kind: str, ext: str) -> str:
    stem = safe_stem(source.name)
    if kind == "audio":
        tag = {"loudness": f"_{st.master_lufs:g}LUFS", "peak": "_piekveilig", "off": ""}[st.master_mode]
        return f"{stem}_master{tag}"
    return f"{stem}_volledig" + ("_sync" if _sync_active(source.info, st) else "")  # type: ignore[arg-type]


def export_audio(ffmpeg: str, source: Source, out_dir: str,
                 progress: Callable[[float, str], None] | None = None,
                 cancel: threading.Event | None = None) -> MasterResult:
    info, st = source.info, source.settings
    assert info is not None
    if not info.has_audio:
        raise ToolError("Deze bron heeft geen geluid.")
    fmt = st.audio_format if st.audio_format in AUDIO_FORMATS else "mp3_320"
    ext = AUDIO_FORMATS[fmt][1]
    v_start, dur = full_range(info, st)
    a_start, sync_f = audio_input(info, st, v_start)
    rate = _rate_for(fmt, info)
    pre = sync_f + [f"aresample={rate}", "aformat=sample_fmts=flt:channel_layouts=stereo"]
    os.makedirs(out_dir, exist_ok=True)
    check_disk_space(out_dir, int(dur * rate * 2 * 3 * (1.0 if fmt == "wav24" else 0.6)))
    rep = (lambda a, b, t: (lambda f: progress(a + (b - a) * f, t))) if progress else (lambda a, b, t: None)

    before = measure(ffmpeg, info.path, a_start, dur, pre, rep(0.0, 0.3, "Niveau meten…"), cancel)
    before.flat_windows, before.scan_windows = clip_scan(ffmpeg, info.path, a_start, dur, cancel=cancel)
    final = reserve_unique(out_dir, _out_name(source, st, "audio", ext), ext)
    part = final + ".part" + ext
    warnings = []
    if before.source_clipped:
        warnings.append(CLIP_WARNING.format(tp=before.true_peak))
    try:
        def make_cmd(chain: list[str]) -> list[str]:
            return ([ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats",
                     "-ss", f"{a_start:.6f}", "-i", info.path, "-map", "0:a:0", "-vn", "-sn", "-dn",
                     "-af", ",".join(pre + chain), "-t", f"{dur:.6f}", "-ar", str(rate), "-ac", "2"]
                    + _audio_codec_args(fmt) + ["-map_metadata", "-1"] + _metadata(source, st)
                    + ["-f", {"wav24": "wav", "flac": "flac"}.get(fmt, "mp3"), part])
        res = render_mastered(ffmpeg, make_cmd, part, before, st, rate, dur, rep, 0.3, 1.0, cancel)
        res.warnings[:0] = warnings
        res.path = final
        os.replace(part, final)
        if progress:
            progress(1.0, "Klaar.")
        return res
    except BaseException:
        for p in (part, final):
            try:
                if p == part or os.path.getsize(p) == 0:
                    os.remove(p)
            except OSError:
                pass
        raise


# ----------------------------------------------------------------------------- video-export
def export_video(ffmpeg: str, source: Source, out_dir: str,
                 progress: Callable[[float, str], None] | None = None,
                 cancel: threading.Event | None = None) -> MasterResult | str:
    """Volledige video (of het gekozen stuk) met gecorrigeerde sync en geluid op niveau.
    Beeld wordt standaard ongewijzigd gekopieerd (snel, geen kwaliteitsverlies)."""
    info, st = source.info, source.settings
    assert info is not None
    if info.audio_only:
        raise ToolError("Deze bron heeft geen beeld; gebruik 'Exporteer audio'.")
    v_start, dur = full_range(info, st)
    copy_v = st.full_video_mode != "reencode"
    if copy_v and info.codec not in VIDEO_COPY_CODECS:
        copy_v = False
    if not copy_v and info.hdr:
        raise ToolError("Opnieuw coderen van HDR-beeld is hier niet mogelijk; kies 'Beeld ongewijzigd'.")
    ext = ".mp4"
    os.makedirs(out_dir, exist_ok=True)
    check_disk_space(out_dir, int(info.size_bytes * dur / max(info.duration, 1) * (1.1 if copy_v else 1.5)))
    rep = (lambda a, b, t: (lambda f: progress(a + (b - a) * f, t))) if progress else (lambda a, b, t: None)
    rate = 48000
    final = reserve_unique(out_dir, _out_name(source, st, "video", ext), ext)
    part = final + ".part.mp4"
    try:
        chain: list[str] = []
        before = None
        a_start = v_start
        if info.has_audio:
            a_start, sync_f = audio_input(info, st, v_start)
            pre = sync_f + [f"aresample={rate}", "aformat=sample_fmts=flt:channel_layouts=stereo"]
            chain = pre
            if st.master_mode != "off":
                before = measure(ffmpeg, info.path, a_start, dur, pre, rep(0.0, 0.25, "Niveau meten…"), cancel)
                before.flat_windows, before.scan_windows = clip_scan(ffmpeg, info.path, a_start, dur, cancel=cancel)
        def make_cmd(achain: list[str]) -> list[str]:
            cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats"]
            if v_start > 0:
                cmd += ["-ss", f"{v_start:.6f}"]
            cmd += ["-i", info.path]
            if info.has_audio:
                cmd += ["-ss", f"{a_start:.6f}", "-i", info.path]
            cmd += ["-map", "0:v:0"]
            if info.has_audio:
                full = chain + achain
                enc = st.audio_encoder if st.audio_encoder in ("aac", "aac_at") else "aac"
                cmd += ["-map", "1:a:0", "-af", ",".join(full) if full else "anull",
                        "-c:a", enc, "-b:a", "320k", "-ar", str(rate), "-ac", "2"]
                if enc == "aac":
                    cmd += ["-aac_pns", "0"]          # zie exporter.audio_encode_args
            if copy_v:
                cmd += ["-c:v", "copy"] + (["-tag:v", "hvc1"] if info.codec == "hevc" else [])
            else:
                cmd += ["-c:v", "libx264", "-preset", st.preset, "-crf", str(int(st.crf)), "-pix_fmt", "yuv420p",
                        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
            return cmd + ["-t", f"{dur:.6f}", "-map_metadata", "-1", "-map_chapters", "-1",
                          "-movflags", "+faststart", "-f", "mp4", part]

        result: MasterResult | str
        if before is not None:
            result = render_mastered(ffmpeg, make_cmd, part, before, st, rate, dur, rep, 0.25, 1.0, cancel,
                                     stream="0:a:0")
            if before.source_clipped:
                result.warnings.insert(0, CLIP_WARNING.format(tp=before.true_peak))
        else:
            run_ffmpeg(make_cmd([]), dur, rep(0.0, 1.0, "Volledige video exporteren…"), cancel)
            result = final
        if not os.path.isfile(part) or os.path.getsize(part) < 1024:
            raise ToolError("De export leverde geen bruikbaar bestand op.")
        os.replace(part, final)
        if isinstance(result, MasterResult):
            result.path = final
        if progress:
            progress(1.0, "Klaar.")
        return result
    except BaseException:
        for p in (part, final):
            try:
                if p == part or os.path.getsize(p) == 0:
                    os.remove(p)
            except OSError:
                pass
        raise


def render_sync_test(ffmpeg: str, info: SourceInfo, st: ExportSettings, center: float, out_path: str,
                     length: float = 20.0, cancel: threading.Event | None = None) -> str:
    """Kort, snel testfragment (960 px breed) met de huidige sync-instelling, om met eigen ogen te controleren."""
    start = max(0.0, min(center - length / 2, info.duration - length))
    length = min(length, info.duration - start)
    shift = av_offset_at(st, start + length / 2) if info.has_audio and not info.audio_only else 0.0
    a_start, pad = audio_window(start, shift)
    af = [f"adelay=delays={pad * 1000:.1f}:all=1"] if pad > 0 else []
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", "-ss", f"{start:.6f}",
           "-i", info.path, "-ss", f"{a_start:.6f}", "-i", info.path, "-map", "0:v:0", "-map", "1:a:0",
           "-vf", "scale=960:-2,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-af", ",".join(af) if af else "anull", "-c:a", "aac", "-b:a", "192k", "-t", f"{length:.3f}",
           "-movflags", "+faststart", out_path]
    run_ffmpeg(cmd, length, None, cancel)
    return out_path


def tool_ok(ffmpeg: str) -> bool:
    from .ffmpeg_tools import encoder_available
    return encoder_available(ffmpeg, "libmp3lame")

