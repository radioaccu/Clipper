"""Export van clips met FFmpeg: filtergraaf, kwaliteit, audio, voortgang, annuleren."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from collections import deque
from dataclasses import dataclass, asdict
from typing import Callable

from .ffmpeg_tools import SourceInfo, ToolError, run_capture
from .platform_paths import WINDOWS_RESERVED, no_window_kwargs
from .layout import compute_layout
from .model import Look, ExportSettings, Clip, Source, OUT_W, OUT_H
from .timecode import format_time


class Cancelled(Exception):
    pass


@dataclass
class Overlay:
    path: str
    cx: float                 # middelpunt als fractie van kaderbreedte
    cy: float
    width_frac: float | None  # None = natuurlijke grootte (bv. voorgerenderde tekst)
    opacity: float = 1.0


# ----------------------------------------------------------------------------- filters
def _fmt(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def video_chain(info: SourceInfo, look: Look, st: ExportSettings) -> list[str]:
    """Alles vóór de overlays: fps → (HDR) → schalen → crop/pad."""
    f: list[str] = []
    if st.fps == "source":
        f.append(f"fps={info.fps_num}/{info.fps_den}")
    else:
        f.append(f"fps={int(st.fps)}")
    keep_hdr = bool(info.hdr) and st.hdr_mode == "keep"
    if info.hdr and not keep_hdr:
        tin = "smpte2084" if info.hdr == "PQ" else "arib-std-b67"
        f.append(f"zscale=tin={tin}:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl=100")
        f.append("format=gbrpf32le")
        f.append("zscale=p=bt709")
        f.append("tonemap=tonemap=hable:desat=0")
        f.append("zscale=t=bt709:m=bt709:r=tv")
        f.append("format=yuv420p")
    lay = compute_layout(info.display_w, info.display_h, look)
    matrix = "bt2020nc" if keep_hdr else "bt709"
    # Expliciete doelmaat uit de gedeelde layout-berekening: correcte pixelverhouding, nooit uitgerekt.
    f.append(f"scale={lay.w}:{lay.h}:flags=lanczos+accurate_rnd+full_chroma_int:"
             f"out_color_matrix={matrix}:out_range=tv")
    f.append("setsar=1")
    if not look.is_color_neutral():
        f.append(f"eq=brightness={_fmt(look.brightness)}:contrast={_fmt(look.contrast)}:"
                 f"saturation={_fmt(look.saturation)}")
    if lay.mode == "fill":
        f.append(f"crop={OUT_W}:{OUT_H}:{-lay.x}:{-lay.y}")
    else:
        f.append(f"pad={OUT_W}:{OUT_H}:{lay.x}:{lay.y}:color=black")
    return f


def out_fps(info: SourceInfo, st: ExportSettings) -> str:
    if st.fps != "source":
        return str(int(st.fps))
    return f"{info.fps_num}/{info.fps_den}"


def _hex(c: str) -> str:
    c = (c or "#5AAAFF").lstrip("#")
    return "0x" + (c if len(c) == 6 else "5AAAFF")


def audio_only_base(info: SourceInfo, look: Look, st: ExportSettings, duration: float, audio_label: str) -> str:
    """Zwart 1080×1350-kader, optioneel met golfvorm uit de audio (alleen voor bronnen zonder beeld)."""
    fps = out_fps(info, st)
    bg = f"color=c=black:s={OUT_W}x{OUT_H}:r={fps}:d={duration + 1:.3f},format=yuv420p"
    if st.audio_visual != "waveform":
        return f"{bg}[base]"
    h = 420
    return (f"{bg}[bg];[{audio_label}]aformat=channel_layouts=mono,"
            f"showwaves=s={OUT_W}x{h}:mode=cline:rate={fps}:colors={_hex(st.wave_color)}:scale=sqrt,"
            f"format=yuva420p[wf];[bg][wf]overlay=x=0:y={(OUT_H - h) // 2}:shortest=1,format=yuv420p[base]")


def build_video_graph(info: SourceInfo, look: Look, st: ExportSettings, overlays: list[Overlay],
                      duration: float, pix_fmt: str, still: bool = False, audio_label: str = "0:a:0") -> str:
    """Bouwt de complete -filter_complex voor beeld (label [v] als uitvoer)."""
    if info.audio_only:
        parts = [audio_only_base(info, look, st, duration, audio_label)]
    else:
        parts = [f"[0:v:0]{','.join(video_chain(info, look, st))}[base]"]
    last = "base"
    for i, ov in enumerate(overlays, start=1):
        chain = []
        if ov.width_frac:
            chain.append(f"scale={int(round(ov.width_frac * OUT_W / 2) * 2)}:-2:flags=lanczos")
        chain.append("format=rgba")
        if ov.opacity < 0.999:
            chain.append(f"colorchannelmixer=aa={_fmt(ov.opacity)}")
        parts.append(f"[{i}:v]{','.join(chain)}[o{i}]")
        parts.append(f"[{last}][o{i}]overlay=x=round({_fmt(ov.cx)}*W-w/2):y=round({_fmt(ov.cy)}*H-h/2):"
                     f"shortest=1:format=auto[ov{i}]")
        last = f"ov{i}"
    tail: list[str] = []
    if not still:
        if look.video_fade_in > 0:
            tail.append(f"fade=t=in:st=0:d={_fmt(look.video_fade_in)}")
        if look.video_fade_out > 0:
            tail.append(f"fade=t=out:st={_fmt(max(0.0, duration - look.video_fade_out))}:"
                        f"d={_fmt(look.video_fade_out)}")
    if still:
        tail.append("scale=in_color_matrix=bt709:in_range=tv:out_range=pc:flags=accurate_rnd")
        tail.append("format=rgb24")
    else:
        tail.append(f"format={pix_fmt}")
    parts.append(f"[{last}]{','.join(tail)}[v]")
    return ";".join(parts)


def build_audio_chain(info: SourceInfo, look: Look, st: ExportSettings, duration: float,
                      measured: dict | None) -> list[str]:
    f: list[str] = []
    gain = look.audio_gain_db
    limiter = False
    if look.mute:
        return ["volume=0"]
    if st.normalize == "loudness" and measured:
        f.append(f"loudnorm=I={_fmt(st.loudness_target)}:TP=-1.5:LRA=11:"
                 f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
                 f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
                 f"offset={measured['target_offset']}:linear=true")
        limiter = True
    elif st.normalize == "peak" and measured and "max_volume" in measured:
        gain += st.peak_target - measured["max_volume"]
        limiter = True
    if abs(gain) > 1e-6:
        f.append(f"volume={_fmt(gain)}dB")
        if gain > 0:
            limiter = True
    if look.audio_fade_in > 0:
        f.append(f"afade=t=in:st=0:d={_fmt(look.audio_fade_in)}")
    if look.audio_fade_out > 0:
        f.append(f"afade=t=out:st={_fmt(max(0.0, duration - look.audio_fade_out))}:"
                 f"d={_fmt(look.audio_fade_out)}")
    if limiter:
        # level=0: de limiter mag het niveau niet zelf bijstellen; alleen pieken boven -1 dBFS afvangen
        f.append("alimiter=limit=0.891:attack=5:release=50:level=0")
    return f


def encoder_args(info: SourceInfo, st: ExportSettings) -> tuple[list[str], str]:
    """Geeft (ffmpeg-argumenten, pix_fmt) voor de gekozen videocodec."""
    keep_hdr = bool(info.hdr) and st.hdr_mode == "keep"
    common_sdr = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                  "-color_range", "tv"]
    if keep_hdr:
        if st.encoder == "h264_videotoolbox":
            raise ToolError("HDR behouden is alleen beschikbaar met software-encoding (HEVC). "
                            "Kies een ander kwaliteitsprofiel of zet HDR op 'Naar SDR omzetten'.")
        trc = "smpte2084" if info.hdr == "PQ" else "arib-std-b67"
        args = ["-c:v", "libx265", "-preset", st.preset, "-crf", str(max(st.crf, 14) if not st.lossless else 0),
                "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", "-colorspace", "bt2020nc",
                "-color_primaries", "bt2020", "-color_trc", trc, "-color_range", "tv"]
        if st.lossless:
            args += ["-x265-params", "lossless=1"]
        return args, "yuv420p10le"
    if st.encoder == "h264_videotoolbox":
        return (["-c:v", "h264_videotoolbox", "-q:v", str(int(st.vt_quality)), "-profile:v", "high",
                 "-pix_fmt", "yuv420p", "-tag:v", "avc1"] + common_sdr, "yuv420p")
    crf = 0 if st.lossless else int(st.crf)
    return (["-c:v", "libx264", "-preset", st.preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
            + common_sdr, "yuv420p")


def audio_encode_args(info: SourceInfo, st: ExportSettings) -> list[str]:
    rate = info.audio_rate if info.audio_rate in (44100, 48000) else 48000
    args = ["-c:a", st.audio_encoder if st.audio_encoder in ("aac", "aac_at") else "aac", "-b:a", f"{int(st.audio_bitrate_k)}k", "-ar", str(rate)]
    if info.audio_channels > 2 or info.audio_channels == 0:
        args += ["-ac", "2"]
    return args


def build_command(ffmpeg: str, info: SourceInfo, start: float, duration: float, look: Look,
                  st: ExportSettings, overlays: list[Overlay], out_path: str,
                  measured: dict | None = None) -> list[str]:
    """Volledige ffmpeg-opdracht als lijst (geen shell!)."""
    venc, pix = encoder_args(info, st)
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats",
           "-ss", f"{start:.6f}", "-i", info.path]
    for ov in overlays:
        cmd += ["-loop", "1", "-framerate", "30", "-i", ov.path]
    audio = info.has_audio
    a_in = "0:a:0"
    prefix = ""
    if info.audio_only and st.audio_visual == "waveform":
        prefix = "[0:a:0]asplit=2[aw][am];"   # één kopie voor de golfvorm, één voor het geluid
        a_in = "am"
    graph = prefix + build_video_graph(info, look, st, overlays, duration, pix,
                                       audio_label="aw" if info.audio_only else "0:a:0")
    if audio:
        achain = build_audio_chain(info, look, st, duration, measured)
        graph += f";[{a_in}]" + (",".join(achain) if achain else "anull") + "[a]"
    cmd += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        cmd += ["-map", "[a]"]
    cmd += ["-t", f"{duration:.6f}", "-fps_mode", "cfr"]
    cmd += venc
    if audio:
        cmd += audio_encode_args(info, st)
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart", "-f", "mp4", out_path]
    return cmd


def build_still_command(ffmpeg: str, info: SourceInfo, t: float, look: Look, st: ExportSettings,
                        overlays: list[Overlay], out_png: str) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", f"{t:.6f}", "-i", info.path]
    for ov in overlays:
        cmd += ["-loop", "1", "-framerate", "30", "-i", ov.path]
    if info.audio_only:
        # alleen 2 s audio inlezen: genoeg voor één golfvorm-frame
        cmd = cmd[:-2] + ["-t", "2", "-i", info.path]
    graph = build_video_graph(info, look, st, overlays, 1.0, "rgb24", still=True)
    cmd += ["-filter_complex", graph, "-map", "[v]", "-frames:v", "1", out_png]
    return cmd


# ----------------------------------------------------------------------------- hulpfuncties
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_stem(name: str, maxlen: int = 80) -> str:
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = unicodedata.normalize("NFC", _BAD.sub("_", stem)).strip(" .")
    stem = (stem[:maxlen].rstrip(" .")) or "clip"
    if stem.split(".")[0].upper() in WINDOWS_RESERVED:      # CON, NUL, COM1 … mogen niet op Windows
        stem = "_" + stem
    return stem


def reserve_unique(directory: str, base: str, ext: str = ".mp4") -> str:
    """Reserveert atomair een nog niet bestaande bestandsnaam (nooit overschrijven)."""
    n = 1
    while True:
        name = f"{base}{ext}" if n == 1 else f"{base}_{n}{ext}"
        path = os.path.join(directory, name)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            return path
        except FileExistsError:
            n += 1


def estimate_bytes(duration: float, st: ExportSettings) -> int:
    """Ruwe, ruime schatting voor de schijfruimtecontrole."""
    if st.lossless:
        mbit = 250.0
    elif st.encoder == "h264_videotoolbox":
        mbit = 8 + st.vt_quality * 0.4
    elif st.crf <= 12:
        mbit = 45.0
    elif st.crf <= 16:
        mbit = 25.0
    else:
        mbit = 12.0
    return int(duration * (mbit + st.audio_bitrate_k / 1000.0) * 1e6 / 8 * 1.15)


def check_disk_space(directory: str, needed: int) -> None:
    free = shutil.disk_usage(directory).free
    if free < needed + 200 * 1024 * 1024:
        raise ToolError(f"Te weinig schijfruimte in {directory}: {free/1e9:.1f} GB vrij, "
                        f"ongeveer {needed/1e9:.1f} GB nodig (plus marge). Kies een andere map of maak ruimte vrij.")


def friendly_error(stderr_tail: str, returncode: int) -> str:
    t = stderr_tail.lower()
    if "no space left" in t:
        return "De schijf is vol tijdens het exporteren. Maak ruimte vrij en probeer opnieuw."
    if "permission denied" in t:
        return "Geen toestemming om te lezen of schrijven. Controleer de rechten van de bronvideo en de exportmap."
    if "no such file" in t:
        return "Een bestand is niet meer beschikbaar (verplaatst of schijf losgekoppeld?)."
    if "moov atom not found" in t or "invalid data found" in t:
        return "De bronvideo lijkt beschadigd of onvolledig (bijv. een opname die niet netjes is afgesloten)."
    if "videotoolbox" in t and ("error" in t or "cannot" in t):
        return "VideoToolbox kon de video niet coderen. Kies een software-profiel (bijv. 'Hoge kwaliteit')."
    if "no path between colorspaces" in t or "zscale" in t:
        return "HDR-omzetting mislukt (zscale ontbreekt of bron is onvolledig getagd). Probeer een andere FFmpeg-build."
    lines = [l for l in stderr_tail.strip().splitlines() if l.strip()]
    return f"FFmpeg gaf een fout (code {returncode}):\n" + "\n".join(lines[-4:])


def measure_audio(ffmpeg: str, info: SourceInfo, start: float, duration: float, st: ExportSettings,
                  cancel: threading.Event | None = None) -> dict | None:
    """Meetpas voor normalisatie (alleen op het te exporteren fragment)."""
    if not info.has_audio or st.normalize == "off":
        return None
    if st.normalize == "loudness":
        af = f"loudnorm=I={_fmt(st.loudness_target)}:TP=-1.5:LRA=11:print_format=json"
    else:
        af = "volumedetect"
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{start:.6f}", "-t", f"{duration:.6f}",
           "-i", info.path, "-vn", "-map", "0:a:0", "-af", af, "-f", "null", "-"]
    cp = run_capture(cmd)
    if cancel and cancel.is_set():
        raise Cancelled()
    if cp.returncode != 0:
        raise ToolError("Kon het audioniveau niet meten voor normalisatie.\n" + cp.stderr[-300:])
    if st.normalize == "peak":
        m = re.search(r"max_volume:\s*(-?[\d.]+) dB", cp.stderr)
        if not m:
            raise ToolError("Geen piekniveau gevonden (stilte?). Zet normalisatie uit voor dit fragment.")
        return {"max_volume": float(m.group(1))}
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", cp.stderr, re.S)
    if not m:
        raise ToolError("Loudness-meting mislukt.")
    data = json.loads(m.group(0))
    if data.get("input_i") in ("-inf", "inf"):
        raise ToolError("Dit fragment is stil; loudness-normalisatie is niet mogelijk.")
    return data


def run_ffmpeg(cmd: list[str], duration: float, progress: Callable[[float], None] | None,
               cancel: threading.Event | None) -> None:
    """Draait ffmpeg met voortgang; ondersteunt annuleren. Gooit Cancelled/ToolError."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, **no_window_kwargs())
    tail: deque[str] = deque(maxlen=60)

    def drain_err():
        for line in proc.stderr:  # type: ignore[union-attr]
            tail.append(line.rstrip())
    t_err = threading.Thread(target=drain_err, daemon=True)
    t_err.start()
    stop_watch = threading.Event()

    def watch():
        while not stop_watch.is_set() and proc.poll() is None:
            if cancel is not None and cancel.wait(0.15):
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                return
            elif cancel is None:
                stop_watch.wait(0.15)
    t_w = threading.Thread(target=watch, daemon=True)
    t_w.start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                    if progress and duration > 0:
                        progress(max(0.0, min(1.0, us / 1e6 / duration)))
                except ValueError:
                    pass
    finally:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        stop_watch.set()
        t_err.join(timeout=2)
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()  # type: ignore[union-attr]
            except Exception:
                pass
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if proc.returncode != 0:
        raise ToolError(friendly_error("\n".join(tail), proc.returncode))


def fill_caption(template: str, source: Source, clip: Clip, index: int) -> str:
    """Vult {title}, {dj}, {start}, {duration}, {source}, {index}, {label}, {url} in. Onbekende {…} blijven staan."""
    title = source.title or os.path.splitext(source.name)[0]
    values = {"title": title, "dj": source.settings.dj_name.strip(), "start": format_time(clip.start, 0, True),
              "duration": f"{clip.duration:.0f}", "source": source.name, "index": f"{index:02d}",
              "label": clip.label or "", "url": source.url}
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return "\n".join(line.strip() for line in out.splitlines()).strip() + "\n"


def _write_extras(ffmpeg: str, source: Source, clip: Clip, final: str, dur: float, index: int) -> None:
    st = source.settings
    stem = os.path.splitext(final)[0]
    if st.export_cover:
        cp = run_capture([ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", f"{dur / 2:.3f}", "-i", final,
                          "-frames:v", "1", "-q:v", "2", stem + "_cover.jpg"], timeout=60)
        if cp.returncode != 0:
            raise ToolError("De clip is gemaakt, maar de omslagfoto niet.\n" + cp.stderr[-200:])
    if st.caption_enabled:
        with open(stem + "_bijschrift.txt", "w", encoding="utf-8") as f:
            f.write(fill_caption(st.caption_template, source, clip, index))


# ----------------------------------------------------------------------------- export
@dataclass
class ClipResult:
    source: str
    clip_id: str
    ok: bool
    path: str = ""
    error: str = ""
    cancelled: bool = False
    start: float = 0.0
    duration: float = 0.0


TextRenderer = Callable[[ExportSettings, str], "str | None"]


def make_overlays(st: ExportSettings, tmpdir: str, text_renderer: TextRenderer | None) -> list[Overlay]:
    ovs: list[Overlay] = []
    if st.logo_path:
        if not os.path.isfile(st.logo_path):
            raise ToolError(f"Logo niet gevonden: {st.logo_path}")
        ovs.append(Overlay(st.logo_path, st.logo_cx, st.logo_cy, st.logo_width, st.logo_opacity))
    if st.dj_name.strip():
        if text_renderer is None:
            raise ToolError("DJ-naam kan niet worden weergegeven (geen tekst-renderer beschikbaar).")
        png = text_renderer(st, tmpdir)
        if png:
            ovs.append(Overlay(png, st.dj_cx, st.dj_cy, None, st.dj_opacity))
    return ovs


def export_clip(ffmpeg: str, source: Source, clip: Clip, out_dir: str, index: int,
                progress: Callable[[float], None] | None = None,
                cancel: threading.Event | None = None,
                text_renderer: TextRenderer | None = None) -> str:
    """Exporteert één clip. Schrijft eerst naar een .part-bestand en hernoemt pas bij succes."""
    info, st = source.info, source.settings
    assert info is not None
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    if not os.path.isfile(info.path):
        raise ToolError(f"Bronvideo niet gevonden (schijf losgekoppeld?):\n{info.path}")
    start = max(0.0, clip.start)
    dur = min(clip.end, info.duration) - start
    if dur <= 0.05:
        raise ToolError("De clip heeft geen geldige lengte (start/einde controleren).")
    check_disk_space(out_dir, estimate_bytes(dur, st))
    if not os.access(out_dir, os.W_OK):
        raise ToolError(f"Geen schrijfrechten in de exportmap:\n{out_dir}")

    base = f"{safe_stem(source.name)}_clip{index:02d}_{format_time(start, force_hours=True).replace(':', '-')}_{dur:.0f}s"
    final = reserve_unique(out_dir, base)
    part = final + ".part"
    tmpdir = tempfile.mkdtemp(prefix="accu_")
    try:
        if cancel and cancel.is_set():
            raise Cancelled()
        overlays = make_overlays(st, tmpdir, text_renderer)
        measured = measure_audio(ffmpeg, info, start, dur, st, cancel)
        cmd = build_command(ffmpeg, info, start, dur, clip.look, st, overlays, part, measured)
        run_ffmpeg(cmd, dur, progress, cancel)
        if not os.path.isfile(part) or os.path.getsize(part) < 1024:
            raise ToolError("De export leverde geen bruikbaar bestand op.")
        os.replace(part, final)
        _write_extras(ffmpeg, source, clip, final, dur, index)
        return final
    except BaseException:
        # Mislukt of geannuleerd: .part verwijderen en de gereserveerde (lege) naam vrijgeven.
        for p in (part, final):
            try:
                if p == part or os.path.getsize(p) == 0:
                    os.remove(p)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_queue(ffmpeg: str, jobs: list[tuple[Source, Clip, int]], out_dir: str,
              on_start: Callable[[int, int, Source, Clip], None] | None = None,
              on_progress: Callable[[int, int, float], None] | None = None,
              cancel: threading.Event | None = None,
              text_renderer: TextRenderer | None = None) -> list[ClipResult]:
    """Verwerkt de wachtrij. Een fout in één clip stopt de rest niet."""
    results: list[ClipResult] = []
    total = len(jobs)
    for n, (src, clip, idx) in enumerate(jobs):
        if cancel and cancel.is_set():
            results.append(ClipResult(src.name, clip.id, False, cancelled=True,
                                      start=clip.start, duration=clip.duration))
            continue
        if on_start:
            on_start(n, total, src, clip)
        try:
            path = export_clip(ffmpeg, src, clip, out_dir, idx,
                               progress=(lambda f, n=n: on_progress(n, total, f)) if on_progress else None,
                               cancel=cancel, text_renderer=text_renderer)
            results.append(ClipResult(src.name, clip.id, True, path=path, start=clip.start,
                                      duration=clip.duration))
        except Cancelled:
            results.append(ClipResult(src.name, clip.id, False, cancelled=True, start=clip.start,
                                      duration=clip.duration))
        except ToolError as e:
            results.append(ClipResult(src.name, clip.id, False, error=str(e), start=clip.start,
                                      duration=clip.duration))
        except Exception as e:  # onverwacht: toch doorgaan met de rest
            results.append(ClipResult(src.name, clip.id, False, error=f"Onverwachte fout: {e}",
                                      start=clip.start, duration=clip.duration))
    return results


def write_summary(out_dir: str, jobs: list[tuple[Source, Clip, int]], results: list[ClipResult]) -> str:
    """Schrijft een tekstoverzicht (+ JSON met volledige instellingen). Geeft het txt-pad terug."""
    from . import __version__
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = reserve_unique(out_dir, f"Radio_ACCU_overzicht_{stamp}", ".txt")
    lines = [f"Radio ACCU Clipper {__version__} – exportoverzicht", datetime.now().strftime("%d-%m-%Y %H:%M"),
             f"Exportmap: {out_dir}", ""]
    data = []
    for (src, clip, idx), res in zip(jobs, results):
        st = src.settings
        status = "OK" if res.ok else ("GEANNULEERD" if res.cancelled else f"MISLUKT: {res.error.splitlines()[0] if res.error else ''}")
        lines.append(f"[{status}] {src.name}  clip {idx:02d}: start {format_time(clip.start, 1, True)}  "
                     f"einde {format_time(clip.end, 1, True)}  duur {clip.duration:.1f}s"
                     + (f"  score {clip.score:.0%}" if clip.score is not None else "")
                     + (f"  – {clip.reason}" if clip.reason else ""))
        if res.ok:
            lines.append(f"      → {os.path.basename(res.path)}")
        enc = "lossless" if st.lossless else (f"VideoToolbox q{st.vt_quality}" if st.encoder == "h264_videotoolbox"
                                              else f"x264 CRF {st.crf} {st.preset}")
        lines.append(f"      {OUT_W}×{OUT_H}, {enc}, fps {st.fps}, audio AAC {st.audio_bitrate_k}k, "
                     f"{clip.look.crop_mode}, normalisatie {st.normalize}, HDR: {st.hdr_mode if src.info and src.info.hdr else '-'}")
        data.append(dict(source=src.path, clip=clip.to_dict(), settings=asdict(st), result=asdict(res)))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(path[:-4] + ".json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
