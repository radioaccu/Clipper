"""Datamodel: clips, instellingen, bronnen en project (JSON-opslag)."""
from __future__ import annotations
import json
import os
import uuid
from dataclasses import dataclass, field, asdict, fields
from typing import Any

from .ffmpeg_tools import SourceInfo

OUT_W, OUT_H = 1080, 1350          # vaste exportmaat (4:5)
PROJECT_VERSION = 3
DEFAULT_CAPTION = "{title}\n\n🎧 {dj}\n📻 Radio ACCU\n\n#radioaccu #djset #livemix"


def _from(cls, d: dict | None):
    d = d or {}
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Look:
    """Per clip instelbaar: beeld en audio."""
    crop_mode: str = "fill"        # "fill" = beeldvullend croppen, "fit" = volledig beeld + zwarte randen
    zoom: float = 1.0              # alleen bij fill; 1.0 – 4.0
    fx: float = 0.5                # horizontale cropkeuze 0..1 (0 = links)
    fy: float = 0.5                # verticale cropkeuze 0..1 (0 = boven)
    brightness: float = 0.0        # -1..1 (eq), 0 = neutraal
    contrast: float = 1.0          # 0..2, 1 = neutraal
    saturation: float = 1.0        # 0..3, 1 = neutraal
    video_fade_in: float = 0.0
    video_fade_out: float = 0.0
    audio_gain_db: float = 0.0
    mute: bool = False
    audio_fade_in: float = 0.0
    audio_fade_out: float = 0.0

    def is_color_neutral(self) -> bool:
        return self.brightness == 0 and self.contrast == 1 and self.saturation == 1


@dataclass
class ExportSettings:
    """Per set instelbaar: kwaliteit, audio-export en branding."""
    profile: str = "very_high"     # zie QUALITY_PROFILES
    encoder: str = "libx264"       # libx264 | h264_videotoolbox
    crf: int = 10
    preset: str = "slow"
    vt_quality: int = 75           # alleen VideoToolbox (1–100)
    lossless: bool = False
    fps: str = "source"            # "source" of "24","25","30","50","60"
    hdr_mode: str = "tonemap"      # "tonemap" (naar SDR) | "keep" (HEVC 10-bit HDR)
    audio_bitrate_k: int = 320
    audio_encoder: str = "aac"     # "aac" (ingebouwd, getest) | "aac_at" (Apple AudioToolbox, alleen op macOS)
    normalize: str = "off"         # off | loudness | peak
    loudness_target: float = -14.0   # LUFS
    peak_target: float = -1.0        # dBFS
    default_duration: float = 30.0
    clip_count: int = 15
    mode: str = "balanced"         # balanced | drops | transitions | high_energy
    # branding
    logo_path: str = ""
    logo_cx: float = 0.86          # midden van logo, als fractie van de frame-breedte/hoogte
    logo_cy: float = 0.075
    logo_width: float = 0.18       # fractie van frame-breedte
    logo_opacity: float = 1.0
    dj_name: str = ""
    dj_cx: float = 0.5
    dj_cy: float = 0.93
    dj_size: int = 56              # tekenhoogte in exportpixels
    dj_color: str = "#FFFFFF"
    dj_outline: bool = True
    dj_opacity: float = 1.0
    dj_bold: bool = True
    # audio-only bronnen (SoundCloud/Mixcloud/mp3): beeld wordt gegenereerd
    audio_visual: str = "waveform"   # "waveform" (golfvorm op zwart) | "black"
    wave_color: str = "#5AAAFF"
    # extra's bij elke geëxporteerde clip
    export_cover: bool = False       # omslagfoto (JPG, 1080×1350) uit het midden van de clip
    caption_enabled: bool = False    # tekstbestand met Instagram-bijschrift
    caption_template: str = DEFAULT_CAPTION


# naam → (encoder, crf, preset, vt_quality, lossless, uitleg)
QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "very_high": dict(label="Zeer hoge kwaliteit (standaard)", encoder="libx264", crf=10, preset="slow",
                      lossless=False,
                      help="CRF 10, slow. Visueel vrijwel niet van de bron te onderscheiden. "
                           "Grote bestanden, trage export."),
    "high": dict(label="Hoge kwaliteit", encoder="libx264", crf=14, preset="medium", lossless=False,
                 help="CRF 14, medium. Nog steeds zeer goed; kleiner en sneller."),
    "compact": dict(label="Compact", encoder="libx264", crf=18, preset="medium", lossless=False,
                    help="CRF 18, medium. Goed voor snel delen; kleine bestanden."),
    "fast_vt": dict(label="Snel – Apple VideoToolbox (hardware)", encoder="h264_videotoolbox", crf=18,
                    preset="medium", lossless=False,
                    help="Gebruikt de media-engine van je Mac: veel sneller, maar bij gelijke bestandsgrootte "
                         "doorgaans lagere kwaliteit dan software-encoding."),
    "lossless": dict(label="Mathematisch lossless (zeer groot)", encoder="libx264", crf=0, preset="veryfast",
                     lossless=True,
                     help="Geen enkel kwaliteitsverlies in de gecodeerde 1080×1350-frames. Bestanden zijn "
                          "enorm (vaak meer dan 100 Mbit/s) en spelen niet af in QuickTime, iPhone of "
                          "Instagram. Alleen voor archivering of verdere bewerking."),
    "custom": dict(label="Aangepast (geavanceerd)", encoder="libx264", crf=10, preset="slow", lossless=False,
                   help="Eigen CRF en encoderpreset."),
}
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
FPS_OPTIONS = ["source", "24", "25", "30", "50", "60"]


def apply_profile(st: ExportSettings, key: str) -> None:
    p = QUALITY_PROFILES[key]
    st.profile = key
    if key != "custom":
        st.encoder, st.crf, st.preset, st.lossless = p["encoder"], p["crf"], p["preset"], p["lossless"]


@dataclass
class Clip:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    start: float = 0.0
    end: float = 30.0
    label: str = ""
    reason: str = ""          # uitleg bij automatische suggestie
    score: float | None = None
    weak: bool = False
    origin: str = "manual"    # manual | auto | tracklist | chapter
    approved: bool = True
    look: Look = field(default_factory=Look)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        c = _from(cls, {k: v for k, v in d.items() if k != "look"})
        c.look = _from(Look, d.get("look"))
        return c


@dataclass
class Source:
    path: str = ""
    info: SourceInfo | None = None
    settings: ExportSettings = field(default_factory=ExportSettings)
    clips: list[Clip] = field(default_factory=list)
    analysis_note: str = ""
    url: str = ""                                   # herkomst bij een gedownloade bron
    title: str = ""
    dl: dict = field(default_factory=dict)          # downloadopties (voor opnieuw downloaden)

    @property
    def name(self) -> str:
        return os.path.basename(self.path) if self.path else (self.title or self.url)

    def to_dict(self) -> dict:
        return dict(path=self.path, info=self.info.to_dict() if self.info else None,
                    settings=asdict(self.settings), clips=[c.to_dict() for c in self.clips],
                    analysis_note=self.analysis_note, url=self.url, title=self.title, dl=self.dl)

    @classmethod
    def from_dict(cls, d: dict) -> "Source":
        s = cls(path=d.get("path", ""))
        s.info = SourceInfo.from_dict(d["info"]) if d.get("info") else None
        s.settings = _from(ExportSettings, d.get("settings"))
        s.clips = [Clip.from_dict(c) for c in d.get("clips", [])]
        s.analysis_note = d.get("analysis_note", "")
        s.url, s.title, s.dl = d.get("url", ""), d.get("title", ""), d.get("dl") or {}
        return s


@dataclass
class Project:
    sources: list[Source] = field(default_factory=list)
    defaults: ExportSettings = field(default_factory=ExportSettings)
    default_look: Look = field(default_factory=Look)
    out_dir: str = ""
    version: int = PROJECT_VERSION

    def save(self, path: str) -> None:
        data = dict(version=self.version, out_dir=self.out_dir, defaults=asdict(self.defaults),
                    default_look=asdict(self.default_look),
                    sources=[s.to_dict() for s in self.sources])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        p = cls(out_dir=d.get("out_dir", ""))
        p.defaults = _from(ExportSettings, d.get("defaults"))
        p.default_look = _from(Look, d.get("default_look"))
        p.sources = [Source.from_dict(s) for s in d.get("sources", [])]
        return p
