"""FFmpeg/ffprobe vinden en bronbestanden analyseren."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from pathlib import Path

from .platform_paths import IS_WIN, bundled_bin_dirs, exe, no_window_kwargs, system_bin_dirs


class ToolError(RuntimeError):
    """Begrijpelijke fout voor de gebruiker."""


def _candidate_dirs() -> list[str]:
    # 1. meegeleverd in de geïnstalleerde app of naast de broncode, 2. Homebrew/systeem
    #    (een vanuit Finder gestarte app kent het Homebrew-pad niet)
    return [str(d) for d in bundled_bin_dirs() + system_bin_dirs()]


def find_tool(name: str) -> str:
    env = os.environ.get(f"ACCU_{name.upper()}")
    if env and os.path.isfile(env):
        return env
    fname = exe(name)
    for d in _candidate_dirs():
        p = os.path.join(d, fname)
        if os.path.isfile(p) and (IS_WIN or os.access(p, os.X_OK)):
            return os.path.abspath(p)
    found = shutil.which(name)
    if found:
        return found
    if IS_WIN:
        hint = "Installeer de app opnieuw met de installer; FFmpeg hoort daarin te zitten."
    else:
        hint = "Installeer FFmpeg (brew install ffmpeg) of installeer de app opnieuw."
    raise ToolError(f"'{name}' is niet gevonden. {hint}")


def run_capture(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    """Voert een commando uit zonder shell (veilig voor spaties/accenten/speciale tekens)."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace", **no_window_kwargs())


def encoder_available(ffmpeg: str, encoder: str) -> bool:
    try:
        out = run_capture([ffmpeg, "-hide_banner", "-encoders"], timeout=20).stdout
    except Exception:
        return False
    # regels zien eruit als " V....D libx264  …" of " A....D aac_at  …" (video én audio)
    return any(line.split()[1:2] == [encoder] for line in out.splitlines()
               if line[:2] in (" V", " A", " S"))


def filter_available(ffmpeg: str, name: str) -> bool:
    try:
        out = run_capture([ffmpeg, "-hide_banner", "-filters"], timeout=20).stdout
    except Exception:
        return False
    return any(len(l.split()) > 1 and l.split()[1] == name for l in out.splitlines())


@dataclass
class SourceInfo:
    path: str = ""
    duration: float = 0.0
    size_bytes: int = 0
    width: int = 0              # opgeslagen resolutie
    height: int = 0
    rotation: int = 0           # graden (0/90/180/270), al in metadata
    sar_num: int = 1
    sar_den: int = 1
    display_w: float = 0.0      # afgebeelde breedte (na rotatie en pixelverhouding)
    display_h: float = 0.0
    codec: str = ""
    pix_fmt: str = ""
    fps_num: int = 25
    fps_den: int = 1
    vfr: bool = False
    color_transfer: str = ""
    color_primaries: str = ""
    color_space: str = ""
    color_range: str = ""
    hdr: str = ""               # "", "PQ" of "HLG"
    dolby_vision: bool = False
    audio_only: bool = False    # bron zonder beeld (mp3, SoundCloud, …): clips krijgen een gegenereerd beeld
    chapters: list = field(default_factory=list)   # [{"start", "end", "title"}]
    has_audio: bool = False
    audio_codec: str = ""
    audio_rate: int = 0
    audio_channels: int = 0
    audio_bitrate: int = 0
    warnings: list = field(default_factory=list)

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den if self.fps_den else 0.0

    @property
    def fps_text(self) -> str:
        return f"{self.fps_num}/{self.fps_den}" if self.fps_den != 1 else str(self.fps_num)

    def describe(self) -> str:
        if self.audio_only:
            return (f"Alleen audio · {self.audio_codec} {self.audio_rate} Hz {self.audio_channels}ch"
                    + (f" · {len(self.chapters)} hoofdstukken" if self.chapters else ""))
        parts = [f"{self.width}×{self.height}"]
        if self.rotation:
            parts.append(f"(gedraaid {self.rotation}° → {self.display_w:.0f}×{self.display_h:.0f})")
        if (self.sar_num, self.sar_den) != (1, 1):
            parts.append(f"pixelverhouding {self.sar_num}:{self.sar_den}")
        parts.append(self.codec.upper())
        parts.append(f"{self.fps:.3f}".rstrip("0").rstrip(".") + " fps" + (" (variabel)" if self.vfr else ""))
        if self.hdr:
            parts.append(f"HDR ({self.hdr})")
        if self.has_audio:
            parts.append(f"audio {self.audio_codec} {self.audio_rate} Hz {self.audio_channels}ch")
        else:
            parts.append("geen audio")
        return " · ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SourceInfo":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _frac(text: str, default: Fraction = Fraction(0)) -> Fraction:
    try:
        if not text or text in ("N/A", "0/0"):
            return default
        return Fraction(text.replace(":", "/"))
    except (ValueError, ZeroDivisionError):
        return default


def _fill_audio_only(info: "SourceInfo", a: dict) -> None:
    """Bron zonder beeld: we werken met een virtueel 1080×1350-kader op 30 fps."""
    info.audio_only = True
    info.width, info.height, info.display_w, info.display_h = 1080, 1350, 1080.0, 1350.0
    info.fps_num, info.fps_den = 30, 1
    info.codec = a.get("codec_name", "audio")
    info.has_audio = True
    info.audio_codec = a.get("codec_name", "")
    info.audio_rate = int(a.get("sample_rate") or 0)
    info.audio_channels = int(a.get("channels") or 0)
    info.audio_bitrate = int(a.get("bit_rate") or 0)
    if info.duration <= 0:
        raise ToolError(f"De duur van dit bestand kon niet worden bepaald:\n{info.path}")


def probe(path: str, ffprobe: str | None = None) -> SourceInfo:
    ffprobe = ffprobe or find_tool("ffprobe")
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ToolError(f"Bestand niet gevonden: {path}")
    cp = run_capture([ffprobe, "-v", "error", "-print_format", "json", "-show_streams",
                      "-show_format", "-show_chapters", path], timeout=120)
    if cp.returncode != 0:
        raise ToolError(f"Kan dit bestand niet lezen als video:\n{path}\n{cp.stderr.strip()[-300:]}")
    data = json.loads(cp.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    if v is None and a is None:
        raise ToolError(f"Geen video- of audiospoor gevonden in:\n{path}")
    info = SourceInfo(path=path)
    info.size_bytes = int(fmt.get("size") or os.path.getsize(path))
    info.duration = float(fmt.get("duration") or (v or a).get("duration") or 0.0)
    for ch in data.get("chapters", []) or []:
        try:
            info.chapters.append({"start": float(ch["start_time"]), "end": float(ch["end_time"]),
                                  "title": (ch.get("tags") or {}).get("title", "")})
        except (KeyError, TypeError, ValueError):
            continue
    if v is None:
        _fill_audio_only(info, a)
        return info
    info.width, info.height = int(v["width"]), int(v["height"])
    info.codec = v.get("codec_name", "?")
    info.pix_fmt = v.get("pix_fmt", "")

    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    if not rot:
        try:
            rot = int(float((v.get("tags") or {}).get("rotate", 0)))
        except ValueError:
            rot = 0
    info.rotation = rot % 360  # ffprobe: -90 → 270 (ffmpeg draait dit automatisch)

    sar = _frac(v.get("sample_aspect_ratio", ""), Fraction(1))
    if sar <= 0:
        sar = Fraction(1)
    info.sar_num, info.sar_den = sar.numerator, sar.denominator

    dw, dh = info.width * float(sar), float(info.height)
    if info.rotation in (90, 270):
        dw, dh = dh, dw
    info.display_w, info.display_h = dw, dh

    avg = _frac(v.get("avg_frame_rate", ""))
    r = _frac(v.get("r_frame_rate", ""))
    fps = avg if avg > 0 else (r if r > 0 else Fraction(25))
    fps = fps.limit_denominator(1001)
    info.fps_num, info.fps_den = fps.numerator, fps.denominator
    info.vfr = bool(avg > 0 and r > 0 and abs(float(avg) - float(r)) / float(r) > 0.02)

    info.color_transfer = v.get("color_transfer", "") or ""
    info.color_primaries = v.get("color_primaries", "") or ""
    info.color_space = v.get("color_space", "") or ""
    info.color_range = v.get("color_range", "") or ""
    if info.color_transfer == "smpte2084":
        info.hdr = "PQ"
    elif info.color_transfer == "arib-std-b67":
        info.hdr = "HLG"
    for sd in v.get("side_data_list", []) or []:
        if "DOVI" in str(sd.get("side_data_type", "")):
            info.dolby_vision = True
    if info.dolby_vision and not info.hdr:
        info.warnings.append("Dolby Vision (profiel zonder HDR10-basislaag) gedetecteerd: "
                             "kleuren kunnen na SDR-conversie afwijken. Controleer de export.")

    if a is not None:
        info.has_audio = True
        info.audio_codec = a.get("codec_name", "")
        info.audio_rate = int(a.get("sample_rate") or 0)
        info.audio_channels = int(a.get("channels") or 0)
        info.audio_bitrate = int(a.get("bit_rate") or 0)
    if info.duration <= 0:
        raise ToolError(f"De duur van dit bestand kon niet worden bepaald:\n{path}")
    return info
