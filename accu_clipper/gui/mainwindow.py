"""Hoofdvenster van Radio ACCU Clipper (PySide6)."""
from __future__ import annotations
import copy
import html
import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QTimer, QUrl
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from .. import exporter as ex
from .. import features as fe
from .. import downloader as dl
from .. import highlights as hl
from ..ffmpeg_tools import ToolError, encoder_available, filter_available, find_tool, probe
from ..model import (Clip, ExportSettings, FPS_OPTIONS, Look, OUT_H, OUT_W, PRESETS, Project, QUALITY_PROFILES,
                     Source, apply_profile)
from ..platform_paths import app_data_dir, file_manager_name, reinstall_hint, videos_dir
from ..timecode import format_time, parse_time
from ..tracklist import parse_tracklist, tracks_from_chapters, tracks_to_ranges
from .branding import render_text_png, text_key
from .dialogs import LinkDialog, TracklistDialog, chapters_as_text, default_download_dir
from .preview import PreviewView
from .timeline import Timeline
from .workers import Worker

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".ts", ".mpg", ".mpeg", ".wmv",
             ".flv", ".mxf", ".mp2", ".3gp"}
PROJECT_EXT = ".accuclip"
SUPPORT_DIR = app_data_dir()
DEFAULT_OUT = str(videos_dir() / "Radio ACCU Clips")


def hint(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet("color: #8a8f98; font-size: 11px;")
    lb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return lb


def spin(lo, hi, step=1, dec=None, suffix=""):
    w = QSpinBox() if dec is None else QDoubleSpinBox()
    if dec is not None:
        w.setDecimals(dec)
    w.setRange(lo, hi)
    w.setSingleStep(step)
    if suffix:
        w.setSuffix(suffix)
    w.setKeyboardTracking(False)
    return w


def scroll_tab(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setWidget(inner)
    return sa


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1560, 960)
        self.setAcceptDrops(True)
        self.project = Project(out_dir=DEFAULT_OUT)
        self.project_path: str | None = None
        self.cur_src: Source | None = None
        self.cur_clip: Clip | None = None
        self.feats: dict[str, fe.Features] = {}
        self.settings_store = QSettings("RadioACCU", "Clipper")
        self._binds: list[tuple] = []
        self._loading = False
        self._filling = False
        self._workers: set[Worker] = set()
        self.bg: Worker | None = None
        self.bg_queue: list[tuple] = []
        self.export_worker: Worker | None = None
        self._errors: list[str] = []
        self._tmp = tempfile.mkdtemp(prefix="accu_ui_")
        self._text_cache: dict[tuple, str] = {}
        self.ffmpeg = self.ffprobe = None
        self.tool_error = ""
        self.has_zscale = self.has_vt = self.has_aac_at = False
        self.ytdlp_status = "yt-dlp: wordt gecontroleerd…"
        self._find_tools()
        self._build_ui()
        self._build_menu()
        self._wire()
        self.refresh_binds()
        self._update_enablement()
        QTimer.singleShot(300, self._offer_restore)
        QTimer.singleShot(900, self._check_ytdlp)

    # ------------------------------------------------------------------ tools
    def _find_tools(self) -> None:
        try:
            self.ffmpeg, self.ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
            self.has_zscale = filter_available(self.ffmpeg, "zscale")
            self.has_vt = encoder_available(self.ffmpeg, "h264_videotoolbox")
            self.has_aac_at = encoder_available(self.ffmpeg, "aac_at")
        except ToolError as e:
            self.tool_error = str(e)

    # ------------------------------------------------------------------ UI-opbouw
    def _build_ui(self) -> None:
        split = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(split)
        split.addWidget(self._build_left())
        split.addWidget(self._build_center())
        split.addWidget(self._build_right())
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setStretchFactor(2, 0)
        split.setSizes([250, 760, 520])
        # statusbalk
        self.status_lbl = QLabel("Klaar. Sleep een of meer video's in dit venster of kies 'Video's toevoegen…'.")
        self.pbar = QProgressBar(); self.pbar.setRange(0, 1000); self.pbar.setFixedWidth(260)
        self.pbar.setTextVisible(True); self.pbar.hide()
        self.btn_cancel = QPushButton("Annuleren"); self.btn_cancel.hide()
        sb = self.statusBar()
        sb.addWidget(self.status_lbl, 1); sb.addPermanentWidget(self.pbar); sb.addPermanentWidget(self.btn_cancel)
        if self.tool_error:
            self.status_lbl.setText("FFmpeg ontbreekt – analyse en export zijn uitgeschakeld.")

    def _build_left(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>DJ-sets</b>"))
        self.src_list = QListWidget()
        self.src_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.src_list, 1)
        row = QHBoxLayout()
        self.btn_add = QPushButton("Video's toevoegen…"); self.btn_rm = QPushButton("Verwijder")
        row.addWidget(self.btn_add); row.addWidget(self.btn_rm); lay.addLayout(row)
        self.btn_link = QPushButton("Link toevoegen (YouTube, SoundCloud…)")
        lay.addWidget(self.btn_link)
        lay.addWidget(hint("Je kunt ook bestanden of links in dit venster slepen. Ook audiobestanden (mp3, wav, m4a) "
                           "werken. Lokale bronbestanden worden alleen gelezen, nooit gekopieerd of gewijzigd."))
        self.info_lbl = QLabel("Geen set geselecteerd."); self.info_lbl.setWordWrap(True)
        self.info_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        gb = QGroupBox("Broninformatie"); gl = QVBoxLayout(gb); gl.addWidget(self.info_lbl)
        lay.addWidget(gb)
        return w

    def _build_center(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        self.preview = PreviewView()
        self.preview.setMinimumSize(360, 450)
        lay.addWidget(self.preview, 1)
        # transport
        tr = QHBoxLayout()
        self.btn_prev = QPushButton("◀︎ frame"); self.btn_play = QPushButton("▶︎ Afspelen")
        self.btn_next = QPushButton("frame ▶︎")
        self.chk_loop = QCheckBox("Alleen clip afspelen"); self.chk_loop.setChecked(True)
        self.time_lbl = QLabel("00:00.00 / 00:00"); self.time_lbl.setMinimumWidth(150)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chk_loop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_prev.setToolTip("Eén frame terug (←). Shift+← = 1 seconde terug")
        self.btn_next.setToolTip("Eén frame vooruit (→). Shift+→ = 1 seconde vooruit")
        self.btn_play.setToolTip("Spatie")
        tr.addWidget(self.btn_prev); tr.addWidget(self.btn_play); tr.addWidget(self.btn_next)
        tr.addWidget(self.chk_loop); tr.addStretch(1); tr.addWidget(self.time_lbl)
        lay.addLayout(tr)
        self.timeline = Timeline()
        lay.addWidget(self.timeline)
        # in/uit-velden
        fr = QHBoxLayout()
        self.ed_start, self.ed_end, self.ed_dur = QLineEdit(), QLineEdit(), QLineEdit()
        for e, ph in ((self.ed_start, "00:00"), (self.ed_end, "00:30"), (self.ed_dur, "30")):
            e.setPlaceholderText(ph); e.setMaximumWidth(110)
            e.setToolTip("Seconden (90), MM:SS (1:30) of HH:MM:SS (0:01:30). Enter om toe te passen.")
        self.btn_set_in = QPushButton("Start = afspeelkop"); self.btn_set_out = QPushButton("Einde = afspeelkop")
        for b in (self.btn_set_in, self.btn_set_out):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        fr.addWidget(QLabel("Start")); fr.addWidget(self.ed_start); fr.addWidget(self.btn_set_in)
        fr.addSpacing(10)
        fr.addWidget(QLabel("Einde")); fr.addWidget(self.ed_end); fr.addWidget(self.btn_set_out)
        fr.addSpacing(10)
        fr.addWidget(QLabel("Duur (s)")); fr.addWidget(self.ed_dur); fr.addStretch(1)
        lay.addLayout(fr)
        lay.addWidget(hint("Sneltoetsen: Spatie = afspelen/pauzeren · ←/→ = 1 frame (Shift: 1 s) · I / O = start/einde op "
                           "afspeelkop. In het voorbeeld: sleep het beeld om te croppen, sleep logo of naam om te verplaatsen."))
        return w

    def _build_right(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(470)
        self.tabs.addTab(self._tab_clips(), "Clips")
        self.tabs.addTab(self._tab_image(), "Beeld")
        self.tabs.addTab(self._tab_quality(), "Kwaliteit")
        self.tabs.addTab(self._tab_audio(), "Audio")
        self.tabs.addTab(self._tab_branding(), "Branding")
        self.tabs.addTab(self._tab_export(), "Export")
        return self.tabs

    # --- binding-helper: koppelt een widget aan een attribuut van het huidige clip.look / set.settings
    def bind(self, widget, getter, attr, kind, after=None, scale=1.0):
        self._binds.append((widget, getter, attr, kind, scale))
        # Alleen het signaal ophalen dat deze soort widget echt heeft (een QComboBox heeft geen valueChanged).
        sig = getattr(widget, {"spin": "valueChanged", "check": "toggled", "combo": "currentIndexChanged",
                               "line": "textChanged"}[kind])

        def changed(*_):
            obj = getter()
            if obj is None or self._loading:
                return
            if kind == "spin":
                val = widget.value() / scale
                if isinstance(widget, QSpinBox):
                    val = int(round(val))          # gehele-getallenvelden (CRF, aantal clips, …)
            elif kind == "check":
                val = widget.isChecked()
            elif kind == "combo":
                val = widget.currentData()
            else:
                val = widget.text()
            setattr(obj, attr, val)
            if after:
                after()
        sig.connect(changed)
        return widget

    def refresh_binds(self) -> None:
        self._loading = True
        for widget, getter, attr, kind, scale in self._binds:
            obj = getter()
            if obj is None:
                continue
            val = getattr(obj, attr)
            if kind == "spin":
                v = val * scale
                widget.setValue(int(round(v)) if isinstance(widget, QSpinBox) else float(v))
            elif kind == "check":
                widget.setChecked(bool(val))
            elif kind == "combo":
                i = widget.findData(val)
                widget.setCurrentIndex(i if i >= 0 else 0)
            else:
                widget.setText(str(val))
        self._loading = False

    def _look(self) -> Look | None:
        return self.cur_clip.look if self.cur_clip else None

    def _set(self) -> ExportSettings | None:
        return self.cur_src.settings if self.cur_src else None

    def _after_look(self) -> None:
        self._refresh_preview()
        self._update_enablement()

    def _after_set(self) -> None:
        self._refresh_preview()
        self._update_enablement()

    # --- tab: clips
    def _tab_clips(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        gb = QGroupBox("Automatische suggesties (heuristische audioanalyse)")
        f = QFormLayout(gb)
        self.cb_mode = QComboBox()
        for k, v in hl.MODES.items():
            self.cb_mode.addItem(v, k)
        self.bind(self.cb_mode, self._set, "mode", "combo")
        self.cb_dur = QComboBox()
        for d in (15, 30, 45, 60):
            self.cb_dur.addItem(f"{d} seconden", d)
        self.cb_dur.addItem("Aangepast…", -1)
        self.sp_dur = spin(5, 600, 1, 1, " s"); self.sp_dur.setValue(30)
        dr = QHBoxLayout(); dr.addWidget(self.cb_dur); dr.addWidget(self.sp_dur)
        self.sp_count = spin(1, 100, 1)
        self.bind(self.sp_count, self._set, "clip_count", "spin")
        f.addRow("Modus", self.cb_mode); f.addRow("Clipduur", dr); f.addRow("Aantal clips", self.sp_count)
        self.btn_analyze = QPushButton("Zoek suggesties")
        f.addRow(self.btn_analyze)
        self.analysis_lbl = hint("Eerst wordt de audio van de set één keer geanalyseerd (voor de waveform). "
                                 "Daarna zijn suggesties direct beschikbaar. Je kunt ook zonder analyse handmatig clips maken.")
        f.addRow(self.analysis_lbl)
        lay.addWidget(gb)
        row = QHBoxLayout()
        self.btn_add_clip = QPushButton("＋ Handmatig (op afspeelkop)")
        self.btn_dup = QPushButton("Dupliceren"); self.btn_del = QPushButton("Verwijderen")
        row.addWidget(self.btn_add_clip); row.addWidget(self.btn_dup); row.addWidget(self.btn_del)
        lay.addLayout(row)
        self.btn_tracks = QPushButton("Clips uit tracklist of hoofdstukken…")
        self.btn_tracks.setToolTip("Plak een tracklist met tijden (bijv. uit de YouTube-beschrijving) of gebruik de "
                                   "hoofdstukken van de video.")
        lay.addWidget(self.btn_tracks)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["✓", "#", "Start", "Duur", "Score", "Reden / label"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 30); self.table.setColumnWidth(1, 30); self.table.setColumnWidth(2, 76)
        self.table.setColumnWidth(3, 50); self.table.setColumnWidth(4, 50)
        self.table.setWordWrap(True)
        lay.addWidget(self.table, 1)
        lay.addWidget(hint("Alleen clips met een vinkje worden geëxporteerd. Oranje op de tijdlijn = zwakke suggestie."))
        return w

    # --- tab: beeld (per clip)
    def _tab_image(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        gb = QGroupBox("Beeldformaat – altijd exact 1080 × 1350 (4:5)")
        f = QFormLayout(gb)
        self.cb_crop = QComboBox()
        self.cb_crop.addItem("Beeldvullend croppen", "fill")
        self.cb_crop.addItem("Volledig beeld behouden (zwarte randen)", "fit")
        self.bind(self.cb_crop, self._look, "crop_mode", "combo", self._after_look)
        self.sp_zoom = spin(1.0, 4.0, 0.05, 2, "×")
        self.bind(self.sp_zoom, self._look, "zoom", "spin", self._after_look)
        self.sp_fx = spin(0, 100, 1, 0, " %"); self.sp_fy = spin(0, 100, 1, 0, " %")
        self.bind(self.sp_fx, self._look, "fx", "spin", self._after_look, scale=100)
        self.bind(self.sp_fy, self._look, "fy", "spin", self._after_look, scale=100)
        self.btn_center = QPushButton("Crop centreren")
        f.addRow("Weergave", self.cb_crop); f.addRow("Zoom in kader", self.sp_zoom)
        f.addRow("Horizontaal (0 = links)", self.sp_fx); f.addRow("Verticaal (0 = boven)", self.sp_fy)
        f.addRow(self.btn_center)
        f.addRow(hint("Het beeld wordt nooit uitgerekt. Sleep het beeld in het voorbeeld om de crop te verplaatsen. "
                      "Rotatiemetadata en pixelverhouding van de bron worden automatisch verwerkt."))
        lay.addWidget(gb)
        ga = QGroupBox("Bronnen zonder beeld (mp3, SoundCloud, …)")
        fa = QFormLayout(ga)
        self.cb_avis = QComboBox()
        self.cb_avis.addItem("Golfvorm van de muziek op zwart", "waveform")
        self.cb_avis.addItem("Zwart beeld", "black")
        self.bind(self.cb_avis, self._set, "audio_visual", "combo")
        self.btn_wcol = QPushButton("Kleur golfvorm…")
        fa.addRow("Beeld", self.cb_avis); fa.addRow(self.btn_wcol)
        fa.addRow(hint("Alleen van toepassing als de set geen beeld heeft. Logo en DJ-naam komen er gewoon overheen. "
                       "Het live voorbeeld toont zwart; de golfvorm zie je via 'Toon exact exportframe' en in de export."))
        lay.addWidget(ga)
        gb2 = QGroupBox("Kleur (neutraal = geen wijziging)")
        f2 = QFormLayout(gb2)
        self.sp_bri = spin(-0.5, 0.5, 0.01, 2); self.sp_con = spin(0.5, 1.5, 0.01, 2); self.sp_sat = spin(0.0, 2.0, 0.01, 2)
        self.bind(self.sp_bri, self._look, "brightness", "spin", self._after_look)
        self.bind(self.sp_con, self._look, "contrast", "spin", self._after_look)
        self.bind(self.sp_sat, self._look, "saturation", "spin", self._after_look)
        self.btn_neutral = QPushButton("Terug naar neutraal")
        f2.addRow("Helderheid", self.sp_bri); f2.addRow("Contrast", self.sp_con); f2.addRow("Verzadiging", self.sp_sat)
        f2.addRow(self.btn_neutral)
        lay.addWidget(gb2)
        gb3 = QGroupBox("Video-fades")
        f3 = QFormLayout(gb3)
        self.sp_vfi = spin(0, 10, 0.1, 1, " s"); self.sp_vfo = spin(0, 10, 0.1, 1, " s")
        self.bind(self.sp_vfi, self._look, "video_fade_in", "spin"); self.bind(self.sp_vfo, self._look, "video_fade_out", "spin")
        f3.addRow("Fade-in (van zwart)", self.sp_vfi); f3.addRow("Fade-out (naar zwart)", self.sp_vfo)
        lay.addWidget(gb3)
        lay.addWidget(hint("Kleur en fades zie je niet in het live voorbeeld, wel in 'Toon exact exportframe' (kleur) en in de export."))
        self.btn_exact = QPushButton("Toon exact exportframe (via FFmpeg)")
        self.btn_apply_look = QPushButton("Deze beeld- en audio-instellingen op alle clips van deze set toepassen")
        self.btn_apply_look_all = QPushButton("…op alle clips van alle sets toepassen")
        self.btn_look_default = QPushButton("Gebruik als standaard voor nieuwe clips")
        for b in (self.btn_exact, self.btn_apply_look, self.btn_apply_look_all, self.btn_look_default):
            lay.addWidget(b)
        lay.addStretch(1)
        return scroll_tab(w)

    # --- tab: kwaliteit (per set)
    def _tab_quality(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        gb = QGroupBox("Videokwaliteit (H.264 MP4)")
        f = QFormLayout(gb)
        self.cb_profile = QComboBox()
        for k, p in QUALITY_PROFILES.items():
            self.cb_profile.addItem(p["label"], k)
        self.profile_help = hint("")
        f.addRow("Profiel", self.cb_profile); f.addRow(self.profile_help)
        lay.addWidget(gb)
        adv = QGroupBox("Geavanceerd"); fa = QFormLayout(adv)
        self.cb_enc = QComboBox()
        self.cb_enc.addItem("Software x264 (beste kwaliteit per MB)", "libx264")
        self.cb_enc.addItem("Apple VideoToolbox (hardware, snel)", "h264_videotoolbox")
        self.sp_crf = spin(0, 35, 1)
        self.cb_preset = QComboBox()
        for p in PRESETS:
            self.cb_preset.addItem(p, p)
        self.sp_vtq = spin(1, 100, 1)
        self.bind(self.cb_enc, self._set, "encoder", "combo", self._adv_changed)
        self.bind(self.sp_crf, self._set, "crf", "spin", self._adv_changed)
        self.bind(self.cb_preset, self._set, "preset", "combo", self._adv_changed)
        self.bind(self.sp_vtq, self._set, "vt_quality", "spin", self._adv_changed)
        fa.addRow("Encoder", self.cb_enc); fa.addRow("CRF (lager = beter)", self.sp_crf)
        fa.addRow("Preset (trager = kleiner)", self.cb_preset); fa.addRow("VideoToolbox-kwaliteit (1–100)", self.sp_vtq)
        fa.addRow(hint("CRF 10 is zeer hoge kwaliteit en levert grote bestanden; CRF 18 is merkbaar kleiner met nog steeds goede "
                       "kwaliteit. Een tragere preset geeft bij gelijke kwaliteit een kleiner bestand, maar duurt langer. "
                       "VideoToolbox is veel sneller maar niet gelijk aan x264 in kwaliteit."))
        lay.addWidget(adv)
        gf = QGroupBox("Beeldsnelheid (FPS)"); ff = QFormLayout(gf)
        self.cb_fps = QComboBox()
        self.cb_fps.addItem("Bronframerate behouden (aanbevolen)", "source")
        for v in FPS_OPTIONS[1:]:
            self.cb_fps.addItem(f"{v} fps", v)
        self.bind(self.cb_fps, self._set, "fps", "combo")
        ff.addRow(self.cb_fps)
        ff.addRow(hint("Een hogere FPS dan de bron maakt de beweging niet vloeiender: er worden alleen frames herhaald. "
                       "Een lagere FPS laat frames weg. Kies 'bron' tenzij een platform iets anders vereist."))
        lay.addWidget(gf)
        gh = QGroupBox("HDR-bronnen"); fh = QFormLayout(gh)
        self.cb_hdr = QComboBox()
        self.cb_hdr.addItem("Omzetten naar SDR met tone-mapping (aanbevolen voor Instagram)", "tonemap")
        self.cb_hdr.addItem("HDR behouden (HEVC 10-bit, minder compatibel)", "keep")
        self.bind(self.cb_hdr, self._set, "hdr_mode", "combo")
        self.hdr_lbl = hint("")
        fh.addRow(self.cb_hdr); fh.addRow(self.hdr_lbl)
        lay.addWidget(gh)
        lay.addWidget(hint("Uitvoer is altijd 1080 × 1350 px. 'Mathematisch lossless' is lossless ten opzichte van de "
                           "1080×1350-frames die FFmpeg maakt, niet ten opzichte van je bronbestand (er wordt geschaald en "
                           "omgezet naar 4:2:0)."))
        lay.addStretch(1)
        return scroll_tab(w)

    # --- tab: audio
    def _tab_audio(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        gb = QGroupBox("AAC-export (per set)"); f = QFormLayout(gb)
        self.cb_abr = QComboBox()
        for k in (128, 192, 256, 320):
            self.cb_abr.addItem(f"{k} kbps" + (" (standaard)" if k == 320 else ""), k)
        self.bind(self.cb_abr, self._set, "audio_bitrate_k", "combo")
        self.cb_aenc = QComboBox()
        self.cb_aenc.addItem("Ingebouwde AAC-encoder", "aac")
        if self.has_aac_at:
            self.cb_aenc.addItem("Apple AudioToolbox AAC (haalt de bitrate nauwkeuriger)", "aac_at")
        self.bind(self.cb_aenc, self._set, "audio_encoder", "combo")
        self.cb_norm = QComboBox()
        self.cb_norm.addItem("Uit (mix ongewijzigd) – standaard", "off")
        self.cb_norm.addItem("Loudness (LUFS)", "loudness")
        self.cb_norm.addItem("Piekniveau (dBFS)", "peak")
        self.sp_lufs = spin(-30, -5, 0.5, 1, " LUFS"); self.sp_peak = spin(-12, 0, 0.5, 1, " dBFS")
        self.bind(self.cb_norm, self._set, "normalize", "combo", self._after_set)
        self.bind(self.sp_lufs, self._set, "loudness_target", "spin"); self.bind(self.sp_peak, self._set, "peak_target", "spin")
        f.addRow("Bitrate", self.cb_abr); f.addRow("Encoder", self.cb_aenc)
        f.addRow("Normalisatie", self.cb_norm); f.addRow("Doel-loudness", self.sp_lufs); f.addRow("Doel-piek", self.sp_peak)
        f.addRow(hint("De gekozen bitrate is een doel; de ingebouwde encoder kan bij eenvoudig materiaal iets lager uitkomen. "
                      "Normalisatie meet alleen het te exporteren fragment. Bij gain of normalisatie voorkomt een limiter "
                      "(−1 dBFS) dat er clipping ontstaat. Zonder gain/normalisatie wordt de mix niet aangeraakt."))
        lay.addWidget(gb)
        gc = QGroupBox("Per clip"); fc = QFormLayout(gc)
        self.sp_gain = spin(-24, 24, 0.5, 1, " dB"); self.chk_mute = QCheckBox("Dempen (stil audiospoor)")
        self.sp_afi = spin(0, 10, 0.1, 1, " s"); self.sp_afo = spin(0, 10, 0.1, 1, " s")
        self.bind(self.sp_gain, self._look, "audio_gain_db", "spin"); self.bind(self.chk_mute, self._look, "mute", "check")
        self.bind(self.sp_afi, self._look, "audio_fade_in", "spin"); self.bind(self.sp_afo, self._look, "audio_fade_out", "spin")
        fc.addRow("Gain", self.sp_gain); fc.addRow(self.chk_mute)
        fc.addRow("Audio-fade-in", self.sp_afi); fc.addRow("Audio-fade-out", self.sp_afo)
        lay.addWidget(gc)
        lay.addStretch(1)
        return scroll_tab(w)

    # --- tab: branding (per set)
    def _tab_branding(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        gl = QGroupBox("ACCU-logo (PNG met transparantie wordt ondersteund)"); f = QFormLayout(gl)
        self.ed_logo = QLineEdit(); self.ed_logo.setReadOnly(True)
        self.btn_logo = QPushButton("Logo kiezen…"); self.btn_logo_rm = QPushButton("Verwijderen")
        r = QHBoxLayout(); r.addWidget(self.btn_logo); r.addWidget(self.btn_logo_rm)
        self.sp_lw = spin(3, 80, 1, 0, " %"); self.sp_lo = spin(5, 100, 5, 0, " %")
        self.sp_lx = spin(0, 100, 1, 0, " %"); self.sp_ly = spin(0, 100, 1, 0, " %")
        self.bind(self.sp_lw, self._set, "logo_width", "spin", self._after_set, scale=100)
        self.bind(self.sp_lo, self._set, "logo_opacity", "spin", self._after_set, scale=100)
        self.bind(self.sp_lx, self._set, "logo_cx", "spin", self._after_set, scale=100)
        self.bind(self.sp_ly, self._set, "logo_cy", "spin", self._after_set, scale=100)
        f.addRow(self.ed_logo); f.addRow(r); f.addRow("Grootte (% van breedte)", self.sp_lw)
        f.addRow("Zichtbaarheid", self.sp_lo); f.addRow("Midden X", self.sp_lx); f.addRow("Midden Y", self.sp_ly)
        f.addRow(hint("Je kunt het logo ook in het voorbeeld verslepen."))
        lay.addWidget(gl)
        gd = QGroupBox("DJ-naam"); fd = QFormLayout(gd)
        self.ed_dj = QLineEdit(); self.ed_dj.setPlaceholderText("Bijv. DJ Voorbeeld (leeg = geen tekst)")
        self.bind(self.ed_dj, self._set, "dj_name", "line", self._text_changed)
        self.sp_dsz = spin(16, 200, 2, 0, " px"); self.chk_dout = QCheckBox("Donkere rand en schaduw (beter leesbaar)")
        self.chk_dbold = QCheckBox("Vet"); self.btn_dcol = QPushButton("Kleur…")
        self.sp_dop = spin(5, 100, 5, 0, " %"); self.sp_dx = spin(0, 100, 1, 0, " %"); self.sp_dy = spin(0, 100, 1, 0, " %")
        self.bind(self.sp_dsz, self._set, "dj_size", "spin", self._text_changed)
        self.bind(self.chk_dout, self._set, "dj_outline", "check", self._text_changed)
        self.bind(self.chk_dbold, self._set, "dj_bold", "check", self._text_changed)
        self.bind(self.sp_dop, self._set, "dj_opacity", "spin", self._after_set, scale=100)
        self.bind(self.sp_dx, self._set, "dj_cx", "spin", self._after_set, scale=100)
        self.bind(self.sp_dy, self._set, "dj_cy", "spin", self._after_set, scale=100)
        fd.addRow("Naam", self.ed_dj); fd.addRow("Tekengrootte", self.sp_dsz); fd.addRow(self.chk_dout)
        fd.addRow(self.chk_dbold); fd.addRow(self.btn_dcol); fd.addRow("Zichtbaarheid", self.sp_dop)
        fd.addRow("Midden X", self.sp_dx); fd.addRow("Midden Y", self.sp_dy)
        fd.addRow(hint("Grootte is in exportpixels (kader = 1080 × 1350). De tekst wordt als afbeelding gerenderd en is in "
                       "preview en export identiek."))
        lay.addWidget(gd)
        lay.addStretch(1)
        return scroll_tab(w)

    # --- tab: export
    def _tab_export(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        gb = QGroupBox("Exportmap"); f = QVBoxLayout(gb)
        self.ed_out = QLineEdit(); self.ed_out.setReadOnly(True)
        row = QHBoxLayout()
        self.btn_out = QPushButton("Map kiezen…"); self.btn_open_out = QPushButton(f"Toon in {file_manager_name()}")
        row.addWidget(self.btn_out); row.addWidget(self.btn_open_out)
        f.addWidget(self.ed_out); f.addLayout(row)
        f.addWidget(hint("Bestaande bestanden worden nooit overschreven: bij een gelijke naam komt er een volgnummer achter."))
        lay.addWidget(gb)
        gx = QGroupBox("Extra's bij elke clip (per set)"); fx = QVBoxLayout(gx)
        self.chk_cover = QCheckBox("Omslagfoto (JPG, 1080 × 1350) uit het midden van de clip")
        self.chk_caption = QCheckBox("Instagram-bijschrift als tekstbestand")
        self.bind(self.chk_cover, self._set, "export_cover", "check")
        self.bind(self.chk_caption, self._set, "caption_enabled", "check", self._update_enablement)
        self.ed_caption = QPlainTextEdit(); self.ed_caption.setFixedHeight(110)
        fx.addWidget(self.chk_cover); fx.addWidget(self.chk_caption); fx.addWidget(self.ed_caption)
        fx.addWidget(hint("Invulvelden: {title} titel van de set · {dj} DJ-naam · {label} tracknaam of label van de clip · "
                          "{start} starttijd · {duration} duur · {url} link · {index} clipnummer · {source} bestandsnaam."))
        lay.addWidget(gx)
        self.btn_export = QPushButton("Exporteer goedgekeurde clips van deze set")
        self.btn_export_all = QPushButton("Exporteer goedgekeurde clips van ALLE sets")
        self.btn_export.setMinimumHeight(34); self.btn_export_all.setMinimumHeight(30)
        lay.addWidget(self.btn_export); lay.addWidget(self.btn_export_all)
        self.btn_apply_set = QPushButton("Instellingen van deze set toepassen op alle sets")
        lay.addWidget(self.btn_apply_set)
        lay.addWidget(hint("Dit kopieert kwaliteit, audio, logo en modus naar alle andere sets (niet de DJ-naam)."))
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("Exportlog verschijnt hier…")
        lay.addWidget(self.log, 1)
        return w

    # ------------------------------------------------------------------ menu en verbindingen
    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("Bestand")
        for text, key, fn in (("Video's toevoegen…", "Ctrl+O", self.add_dialog),
                              ("Link toevoegen…", "Ctrl+L", lambda: self.add_link_dialog()),
                              ("Project openen…", "Ctrl+Shift+O", self.open_project),
                              ("Project opslaan", "Ctrl+S", lambda: self.save_project(False)),
                              ("Project opslaan als…", "Ctrl+Shift+S", lambda: self.save_project(True)),
                              ("Exportmap kiezen…", "", self.choose_out_dir)):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            m.addAction(a)
        h = self.menuBar().addMenu("Help")
        a = QAction(f"Over {APP_NAME}", self)
        a.triggered.connect(self.about)
        h.addAction(a)
        a = QAction("yt-dlp (downloader voor links) bijwerken", self)
        a.triggered.connect(self.update_ytdlp)
        h.addAction(a)

    def about(self) -> None:
        QMessageBox.information(self, APP_NAME, f"{APP_NAME} {__version__}\n\nLokale clip-tool voor DJ-sets. "
                                "Alle verwerking gebeurt op deze Mac met FFmpeg; er is geen cloud of abonnement.\n\n"
                                f"FFmpeg: {self.ffmpeg or 'niet gevonden'}\n{self.ytdlp_status}")

    def _wire(self) -> None:
        self.btn_add.clicked.connect(self.add_dialog); self.btn_rm.clicked.connect(self.remove_source)
        self.btn_link.clicked.connect(lambda: self.add_link_dialog())
        self.btn_tracks.clicked.connect(self.tracks_dialog)
        self.btn_wcol.clicked.connect(self.choose_wave_color)
        self.ed_caption.textChanged.connect(self._caption_changed)
        self.src_list.currentRowChanged.connect(self.on_select_source)
        self.btn_cancel.clicked.connect(self.cancel_current)
        self.btn_prev.clicked.connect(lambda: self.step_frames(-1)); self.btn_next.clicked.connect(lambda: self.step_frames(1))
        self.btn_play.clicked.connect(self.toggle_play)
        pl = self.preview.player
        pl.positionChanged.connect(self.on_position)
        pl.playbackStateChanged.connect(self.on_state)
        pl.errorOccurred.connect(self.on_player_error)
        self.preview.cropDragged.connect(self.on_crop_drag)
        self.preview.logoMoved.connect(self.on_logo_move); self.preview.textMoved.connect(self.on_text_move)
        t = self.timeline
        t.seekRequested.connect(self.seek); t.rangeEdited.connect(self.on_range_edit)
        t.rangeFinished.connect(self._clip_modified_final); t.clipClicked.connect(self.select_clip_by_id)
        self.ed_start.editingFinished.connect(lambda: self.apply_field("start"))
        self.ed_end.editingFinished.connect(lambda: self.apply_field("end"))
        self.ed_dur.editingFinished.connect(lambda: self.apply_field("dur"))
        self.btn_set_in.clicked.connect(lambda: self.set_point("start")); self.btn_set_out.clicked.connect(lambda: self.set_point("end"))
        self.cb_dur.currentIndexChanged.connect(self.on_dur_combo); self.sp_dur.valueChanged.connect(self.on_dur_custom)
        self.btn_analyze.clicked.connect(self.analyze)
        self.btn_add_clip.clicked.connect(self.add_manual_clip); self.btn_dup.clicked.connect(self.duplicate_clip)
        self.btn_del.clicked.connect(self.delete_clip)
        self.table.itemSelectionChanged.connect(self.on_table_select); self.table.itemChanged.connect(self.on_table_check)
        self.btn_center.clicked.connect(self.center_crop); self.btn_neutral.clicked.connect(self.neutral_color)
        self.btn_exact.clicked.connect(self.show_exact)
        self.btn_apply_look.clicked.connect(lambda: self.apply_look(False)); self.btn_apply_look_all.clicked.connect(lambda: self.apply_look(True))
        self.btn_look_default.clicked.connect(self.look_as_default)
        self.cb_profile.currentIndexChanged.connect(self.on_profile)
        self.btn_logo.clicked.connect(self.choose_logo); self.btn_logo_rm.clicked.connect(self.remove_logo)
        self.btn_dcol.clicked.connect(self.choose_color)
        self.btn_out.clicked.connect(self.choose_out_dir); self.btn_open_out.clicked.connect(self.open_out_dir)
        self.btn_export.clicked.connect(lambda: self.start_export([self.cur_src] if self.cur_src else []))
        self.btn_export_all.clicked.connect(lambda: self.start_export(list(self.project.sources)))
        self.btn_apply_set.clicked.connect(self.apply_set_all)
        self.project.out_dir = str(self.settings_store.value("out_dir", DEFAULT_OUT))
        self.ed_out.setText(self.project.out_dir)

    # ------------------------------------------------------------------ achtergrondtaken
    def _start_worker(self, worker: Worker, on_done, on_fail, label: str, track_bg: bool = True) -> None:
        self._workers.add(worker)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        if track_bg:
            self.bg = worker
        self.pbar.setValue(0); self.pbar.show(); self.btn_cancel.show()
        self.status_lbl.setText(label)
        worker.progress.connect(lambda f, t, wk=worker: self._on_progress(wk, f, t))

        def done(res):
            if track_bg:
                self.bg = None
            on_done(res)
            self._after_task()

        def failed(msg):
            if track_bg:
                self.bg = None
            on_fail(msg)
            self._after_task()

        def cancelled():
            if track_bg:
                self.bg = None
            self.status_lbl.setText("Geannuleerd.")
            self._after_task()
        worker.done.connect(done); worker.failed.connect(failed); worker.cancelled.connect(cancelled)
        worker.start()

    def _on_progress(self, worker: Worker, frac: float, text: str) -> None:
        self.pbar.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        self.pbar.setFormat(f"{frac:.0%}")
        if text:
            self.status_lbl.setText(text)

    def _after_task(self) -> None:
        if self.bg is None and self.export_worker is None:
            if self.bg_queue:
                self._pump()
            else:
                self.pbar.hide(); self.btn_cancel.hide()
                if self.status_lbl.text().startswith(("Audio", "Bestand", "Exporteren")):
                    self.status_lbl.setText("Klaar.")
                if self._errors:
                    QMessageBox.warning(self, "Niet alles is gelukt", "\n\n".join(self._errors))
                    self._errors.clear()
        self._update_enablement()

    def _pump(self) -> None:
        if self.bg is not None or self.export_worker is not None or not self.bg_queue:
            return
        kind, src = self.bg_queue.pop(0)
        if src not in self.project.sources:
            return self._pump()
        if kind == "link":
            return self._start_link(src)
        if kind == "probe":
            w = Worker(lambda wk, p=src.path: probe(p, self.ffprobe))
            self._start_worker(w, lambda info, s=src: self._probe_done(s, info),
                               lambda msg, s=src: self._probe_failed(s, msg), f"Bestand lezen: {src.name}…")
        else:
            info = src.info
            assert info is not None
            w = Worker(lambda wk, p=src.path, d=info.duration: fe.extract(
                self.ffmpeg, p, d, progress=lambda f: wk.report(f, f"Audio analyseren voor waveform: {os.path.basename(p)}"),
                cancel=wk.cancel_event))
            self._start_worker(w, lambda f, s=src: self._extract_done(s, f),
                               lambda msg, s=src: self._extract_failed(s, msg),
                               f"Audio analyseren voor waveform: {src.name}…")

    def _enqueue(self, kind: str, src: Source) -> None:
        self.bg_queue.append((kind, src))
        self._pump()

    def cancel_current(self) -> None:
        if self.export_worker is not None:
            self.export_worker.cancel()
            self.status_lbl.setText("Export wordt geannuleerd… (tijdelijke bestanden worden opgeruimd)")
        elif self.bg is not None:
            # downloads en analyses stoppen; het (snelle) inlezen van bestanden laten we doorgaan
            self.bg_queue = [(k, s) for k, s in self.bg_queue if k == "probe"]
            self.bg.cancel()
            self._drop_placeholders()

    # ------------------------------------------------------------------ bronnen
    def _video_filter(self) -> str:
        return "Video's (" + " ".join("*" + e for e in sorted(VIDEO_EXT)) + ");;Alle bestanden (*)"

    def add_dialog(self) -> None:
        start = str(self.settings_store.value("last_dir", str(videos_dir())))
        files, _ = QFileDialog.getOpenFileNames(self, "Kies een of meer video's", start, self._video_filter())
        if files:
            self.settings_store.setValue("last_dir", os.path.dirname(files[0]))
            self.add_files(files)

    def add_files(self, paths: list[str]) -> None:
        if self.tool_error:
            QMessageBox.critical(self, "FFmpeg niet gevonden", self.tool_error)
            return
        expanded: list[str] = []
        for p in paths:
            if os.path.isdir(p):
                expanded += sorted(str(f) for f in Path(p).iterdir() if f.suffix.lower() in VIDEO_EXT)
            else:
                expanded.append(p)
        for p in expanded:
            ap = os.path.abspath(p)
            if any(s.path == ap for s in self.project.sources) or not os.path.isfile(ap):
                continue
            src = Source(path=ap, settings=copy.deepcopy(self.project.defaults))
            self.project.sources.append(src)
            self.src_list.addItem(QListWidgetItem(f"{src.name}\nbezig met lezen…"))
            self._enqueue("probe", src)

    def _src_item(self, src: Source) -> QListWidgetItem | None:
        try:
            return self.src_list.item(self.project.sources.index(src))
        except ValueError:
            return None

    def _update_src_item(self, src: Source) -> None:
        it = self._src_item(src)
        if it is None:
            return
        if src.info:
            n = sum(1 for c in src.clips if c.approved)
            missing = "" if os.path.isfile(src.path) else "  ⚠︎ bestand ontbreekt"
            it.setText(f"{src.name}\n{format_time(src.info.duration, 0, True)} · {n}/{len(src.clips)} clips goedgekeurd{missing}")
        it.setToolTip(src.path)

    def _probe_done(self, src: Source, info) -> None:
        if src not in self.project.sources:
            return
        if not info.chapters and src.dl.get("chapters") and not src.dl.get("section"):
            info.chapters = list(src.dl["chapters"])
        src.info = info
        if src.dl.get("tracks") and not src.dl.get("tracks_done"):
            src.dl["tracks_done"] = True
            self._auto_tracks(src)
        self._update_src_item(src)
        if self.cur_src is None:
            self.src_list.setCurrentRow(self.project.sources.index(src))
        elif src is self.cur_src:
            self._reload_current()
        if info.has_audio:
            cached = fe.load_cached(src.path)
            if cached is not None:
                self.feats[src.path] = cached
                if src is self.cur_src:
                    self.timeline.set_peaks(cached.peak)
            else:
                self._enqueue("extract", src)
        else:
            src.analysis_note = "Deze video heeft geen audio: automatische suggesties zijn niet mogelijk. Handmatig knippen kan wel."

    def _probe_failed(self, src: Source, msg: str) -> None:
        self._errors.append(msg)
        self._remove_source_obj(src)

    def _extract_done(self, src: Source, f: fe.Features) -> None:
        self.feats[src.path] = f
        if src is self.cur_src:
            self.timeline.set_peaks(f.peak)
        self.status_lbl.setText(f"Audio-analyse klaar: {src.name}")

    def _extract_failed(self, src: Source, msg: str) -> None:
        src.analysis_note = msg
        self._errors.append(f"{src.name}: {msg}")

    # ------------------------------------------------------------------ links
    def _check_ytdlp(self) -> None:
        def job(w: Worker):
            dl.find_ytdlp()
            v = dl.version()
            return v, dl.deno_available()

        def done(res):
            v, deno = res
            self.ytdlp_status = f"yt-dlp {v} gevonden." + ("" if deno else
                " ⚠︎ Deno ontbreekt: YouTube-downloads kunnen mislukken. " + reinstall_hint())

        def failed(msg):
            self.ytdlp_status = "⚠︎ " + msg
        w = Worker(job)
        self._workers.add(w)
        w.finished.connect(lambda wk=w: self._workers.discard(wk))
        w.done.connect(done); w.failed.connect(failed)
        w.start()

    def update_ytdlp(self) -> None:
        def done(v):
            self.ytdlp_status = f"yt-dlp {v} gevonden."
            QMessageBox.information(self, "yt-dlp", f"yt-dlp is bijgewerkt naar versie {v}.")
        self._start_worker(Worker(lambda w: dl.update()), done,
                           lambda m: QMessageBox.warning(self, "yt-dlp bijwerken", m),
                           "yt-dlp bijwerken…", track_bg=False)

    def add_link_dialog(self, prefill: str = "") -> None:
        if self.tool_error:
            QMessageBox.critical(self, "FFmpeg niet gevonden", self.tool_error)
            return
        if not prefill:
            clip = QApplication.clipboard().text() or ""
            prefill = "\n".join(dl.extract_urls(clip))
        last = dict(quality=self.settings_store.value("link_quality", "best"),
                    cookies=self.settings_store.value("link_cookies", ""),
                    tracks=self.settings_store.value("link_tracks", False, type=bool))
        dlg = LinkDialog(self, prefill, str(self.settings_store.value("dl_dir", default_download_dir())),
                         self.ytdlp_status, last)
        if not dlg.exec():          # 0 = geannuleerd, 1 = OK
            return
        o = dlg.result_opts
        for k in ("quality", "cookies", "tracks"):
            self.settings_store.setValue(f"link_{k}", o[k])
        self.settings_store.setValue("dl_dir", o["dir"])
        self.add_links(o)

    def add_links(self, o: dict) -> None:
        """Zet links in de wachtrij. o: urls, quality, cookies, section, tracks, dir."""
        for u in o["urls"]:
            if any(x.url == u for x in self.project.sources):
                self.status_lbl.setText(f"Deze link staat al in de lijst: {u}")
                continue
            self._add_placeholder(u, u, o)

    def _add_placeholder(self, url: str, title: str, o: dict) -> Source:
        opts = {k: o.get(k) for k in ("quality", "cookies", "section", "tracks", "dir")}
        src = Source(path="", url=url, title=title, settings=copy.deepcopy(self.project.defaults), dl=opts)
        self.project.sources.append(src)
        self.src_list.addItem(QListWidgetItem(f"⬇︎ {title}\nwacht op download…"))
        self._enqueue("link", src)
        return src

    def _set_placeholder_text(self, src: Source, text: str) -> None:
        it = self._src_item(src)
        if it is not None and not src.path:
            it.setText(f"⬇︎ {src.title}\n{text}")

    def _drop_placeholders(self) -> None:
        for x in [x for x in self.project.sources if not x.path]:
            self._remove_source_obj(x)

    def _start_link(self, src: Source) -> None:
        o = dict(src.dl)

        def job(wk: Worker):
            wk.report(0.0, f"Gegevens ophalen: {src.url}")
            info = dl.fetch_info(src.url, o.get("cookies") or "")
            if info.is_playlist:
                return "playlist", info, ""
            if info.is_live:
                raise ToolError("Dit is een livestream die nog bezig is. Download hem nadat de uitzending is afgelopen.")
            sec = tuple(o["section"]) if o.get("section") else None
            expected = info.filesize
            if sec and info.duration and expected:
                expected = int(expected * (sec[1] - sec[0]) / info.duration)
            path = dl.download(src.url, o["dir"], o.get("quality") or "best", o.get("cookies") or "", sec, expected,
                               progress=lambda f, t: wk.report(f, t), cancel=wk.cancel_event)
            return "file", info, path
        w = Worker(job)
        w.source = src
        w.progress.connect(lambda f, t, s=src: self._set_placeholder_text(s, t))
        self._start_worker(w, lambda r, s=src: self._link_done(s, r), lambda m, s=src: self._link_failed(s, m),
                           f"Link ophalen: {src.url}")

    def _link_done(self, src: Source, res) -> None:
        kind, info, path = res
        if src not in self.project.sources:
            return
        if kind == "playlist":
            n = len(info.entries)
            self._remove_source_obj(src)
            if n == 0:
                self._errors.append(f"De afspeellijst '{info.title or src.url}' is leeg of niet toegankelijk.")
                return
            if QMessageBox.question(self, "Afspeellijst", f"'{info.title or src.url}' is een afspeellijst met {n} "
                                    f"items. Alle {n} downloaden?") == QMessageBox.StandardButton.Yes:
                for e in info.entries:
                    if not any(x.url == e["url"] for x in self.project.sources):
                        self._add_placeholder(e["url"], e.get("title") or e["url"], src.dl)
            return
        dup = next((x for x in self.project.sources if x.path == path and x is not src), None)
        if dup is not None:
            self._remove_source_obj(src)
            self.status_lbl.setText(f"Dit bestand staat al in de lijst: {dup.name}")
            return
        src.path = path
        src.title = info.title or os.path.splitext(os.path.basename(path))[0]
        src.dl.update(description=info.description, uploader=info.uploader,
                      chapters=[] if src.dl.get("section") else info.chapters)
        it = self._src_item(src)
        if it is not None:
            it.setText(f"{src.title}\nbezig met lezen…")
        self._enqueue("probe", src)

    def _link_failed(self, src: Source, msg: str) -> None:
        self._errors.append(f"{src.title}:\n{msg}")
        self._remove_source_obj(src)

    # ------------------------------------------------------------------ tracklist / hoofdstukken
    def _auto_tracks(self, src: Source) -> None:
        dur = src.info.duration
        tracks = parse_tracklist(src.dl.get("description", ""), dur)
        what = "tracklist uit de beschrijving"
        if not tracks and src.info.chapters:
            tracks, what = tracks_from_chapters(src.info.chapters, dur), "hoofdstukken"
        if not tracks:
            src.analysis_note = "Geen tracklist of hoofdstukken gevonden; gebruik 'Zoek suggesties' of maak handmatig clips."
            return
        ranges = tracks_to_ranges(tracks, src.settings.default_duration, 0.0, dur, True)
        self._add_track_clips(src, ranges, True)
        src.analysis_note = f"{len(ranges)} clips gemaakt uit de {what}."

    def tracks_dialog(self) -> None:
        src = self.cur_src
        if not src or not src.info:
            return
        text = src.dl.get("description", "")
        if not parse_tracklist(text, src.info.duration) and src.info.chapters:
            text = chapters_as_text(src.info.chapters)
        dlg = TracklistDialog(self, text, src.info.duration, src.settings.default_duration)
        if not dlg.exec():          # 0 = geannuleerd, 1 = OK
            return
        self._add_track_clips(src, dlg.ranges, dlg.replace.isChecked())
        self.analysis_lbl.setText(f"{len(dlg.ranges)} clips gemaakt uit de tracklist.")

    def _add_track_clips(self, src: Source, ranges: list, replace: bool) -> None:
        if replace:
            src.clips = [c for c in src.clips if c.origin != "tracklist"]
        new = [Clip(start=a, end=b, label=t, reason="Uit tracklist", origin="tracklist",
                    look=copy.deepcopy(self.project.default_look)) for a, b, t in ranges]
        src.clips += new
        src.clips.sort(key=lambda c: c.start)
        self._update_src_item(src)
        if src is self.cur_src:
            self._fill_table()
            if new:
                self.select_clip(new[0])
            else:
                self._after_selection()

    def choose_wave_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog
        st = self._set()
        if st is None:
            return
        col = QColorDialog.getColor(QColor(st.wave_color), self, "Kleur van de golfvorm")
        if col.isValid():
            st.wave_color = col.name()
            self.btn_wcol.setStyleSheet(f"background-color: {st.wave_color}; color: #222; border: 1px solid #666;")

    def _caption_changed(self) -> None:
        st = self._set()
        if st is not None and not self._loading:
            st.caption_template = self.ed_caption.toPlainText()

    def remove_source(self) -> None:
        row = self.src_list.currentRow()
        if row < 0:
            return
        src = self.project.sources[row]
        if src.clips and QMessageBox.question(self, "Set verwijderen",
                                              f"'{src.name}' en de bijbehorende clips uit het project halen?\n"
                                              "(Het videobestand zelf blijft onaangetast.)") != QMessageBox.StandardButton.Yes:
            return
        if self.bg is not None and getattr(self.bg, "source", None) is src:
            self.bg.cancel()
        self._remove_source_obj(src)

    def _remove_source_obj(self, src: Source) -> None:
        if src not in self.project.sources:
            return
        row = self.project.sources.index(src)
        self.bg_queue = [(k, s) for k, s in self.bg_queue if s is not src]
        was_current = src is self.cur_src
        self.project.sources.pop(row)
        if src.path:
            self.feats.pop(src.path, None)
        self.src_list.blockSignals(True)
        self.src_list.takeItem(row)
        self.src_list.blockSignals(False)
        if not was_current:
            if self.cur_src in self.project.sources:
                self.src_list.blockSignals(True)
                self.src_list.setCurrentRow(self.project.sources.index(self.cur_src))
                self.src_list.blockSignals(False)
            return
        self.cur_src = None
        if self.project.sources:
            self.src_list.setCurrentRow(min(row, len(self.project.sources) - 1))
            self.on_select_source(self.src_list.currentRow())
        else:
            self.on_select_source(-1)

    def on_select_source(self, row: int) -> None:
        if row < 0 or row >= len(self.project.sources):
            self.cur_src = None; self.cur_clip = None
            self.preview.set_source(None)
            self.timeline.set_media(1.0, None)
            self.timeline.set_selection(None); self.timeline.set_others([])
            self._fill_table(); self._refresh_all()
            return
        src = self.project.sources[row]
        if src is self.cur_src:
            return
        self.cur_src, self.cur_clip = src, None
        self._reload_current()

    def _reload_current(self) -> None:
        src = self.cur_src
        if src is None:
            return
        self.preview.set_source(src.path if src.info else None)
        f = self.feats.get(src.path)
        self.timeline.set_media(src.info.duration if src.info else 1.0, f.peak if f is not None else None)
        self.analysis_lbl.setText(src.analysis_note or "Eerst wordt de audio van de set één keer geanalyseerd (voor de "
                                  "waveform). Daarna zijn suggesties direct beschikbaar.")
        self._fill_table()
        if src.clips:
            self.select_clip(src.clips[0])
        else:
            self.cur_clip = None
            self._after_selection()

    def _update_info(self) -> None:
        src = self.cur_src
        if not src or not src.info:
            if not src:
                self.info_lbl.setText("Geen set geselecteerd.")
            elif not src.path:
                self.info_lbl.setText(f"Wordt gedownload:<br>{html.escape(src.url)}")
            else:
                self.info_lbl.setText("Bestand wordt gelezen…")
            self.hdr_lbl.setText("")
            return
        i = src.info
        lines = [f"<b>{html.escape(src.title or src.name)}</b>", f"Duur: {format_time(i.duration, 0, True)}",
                 f"Grootte: {i.size_bytes/1e9:.2f} GB", html.escape(i.describe())]
        if src.url:
            lines.append(f"Link: {html.escape(src.url)}")
        if i.chapters:
            lines.append(f"{len(i.chapters)} hoofdstukken (gebruik 'Clips uit tracklist of hoofdstukken')")
        lines += [f"⚠︎ {html.escape(w)}" for w in i.warnings]
        if not os.path.isfile(src.path):
            lines.append("⚠︎ Het bestand is niet meer beschikbaar op deze locatie.")
        self.info_lbl.setText("<br>".join(lines))
        if i.hdr:
            msg = f"HDR gedetecteerd ({i.hdr}). "
            msg += ("Wordt met tone-mapping naar SDR omgezet, zodat het beeld niet flets of te donker wordt."
                    if src.settings.hdr_mode == "tonemap" else
                    "Wordt als HEVC 10-bit HDR bewaard; dat is minder breed compatibel (Instagram kan dit anders tonen).")
            if not self.has_zscale and src.settings.hdr_mode == "tonemap":
                msg += " ⚠︎ Deze FFmpeg-build mist 'zscale': omzetten is niet mogelijk."
            self.hdr_lbl.setText(msg)
        else:
            self.hdr_lbl.setText("Geen HDR gedetecteerd in deze bron: deze instelling is niet van toepassing.")

    # ------------------------------------------------------------------ clips
    def _sort_clips(self) -> None:
        if self.cur_src:
            self.cur_src.clips.sort(key=lambda c: c.start)

    def _fill_table(self) -> None:
        self._filling = True
        clips = self.cur_src.clips if self.cur_src else []
        self.table.setRowCount(len(clips))
        for i, c in enumerate(clips):
            self._set_row(i, c)
        self._filling = False

    def _set_row(self, i: int, c: Clip) -> None:
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        chk = QTableWidgetItem()
        chk.setFlags(flags | Qt.ItemFlag.ItemIsUserCheckable)
        chk.setCheckState(Qt.CheckState.Checked if c.approved else Qt.CheckState.Unchecked)
        self.table.setItem(i, 0, chk)
        texts = [str(i + 1), format_time(c.start, 0), f"{c.duration:.0f}s",
                 f"{c.score:.0%}" if c.score is not None else "—", c.label or c.reason or "Handmatig"]
        for col, tx in enumerate(texts, start=1):
            it = QTableWidgetItem(tx)
            it.setFlags(flags)
            if c.weak:
                it.setForeground(QBrush(QColor(230, 160, 40)))
            if col == 5:
                it.setToolTip(c.reason or tx)
            self.table.setItem(i, col, it)

    def _row_of(self, clip: Clip | None) -> int:
        if clip is None or self.cur_src is None:
            return -1
        for i, c in enumerate(self.cur_src.clips):
            if c is clip:
                return i
        return -1

    def on_table_select(self) -> None:
        if self._filling or self.cur_src is None:
            return
        row = self.table.currentRow()
        if 0 <= row < len(self.cur_src.clips) and self.cur_src.clips[row] is not self.cur_clip:
            self.select_clip(self.cur_src.clips[row])

    def on_table_check(self, item) -> None:
        if self._filling or self.cur_src is None or item.column() != 0:
            return
        row = item.row()
        if 0 <= row < len(self.cur_src.clips):
            self.cur_src.clips[row].approved = item.checkState() == Qt.CheckState.Checked
            self._update_src_item(self.cur_src)

    def select_clip_by_id(self, cid: str) -> None:
        if self.cur_src:
            for c in self.cur_src.clips:
                if c.id == cid:
                    self.select_clip(c)
                    return

    def select_clip(self, clip: Clip, seek: bool = True) -> None:
        self.cur_clip = clip
        row = self._row_of(clip)
        if row >= 0:
            self._filling = True
            self.table.selectRow(row)
            self._filling = False
        self._after_selection()
        if seek:
            self.seek(clip.start)

    def _after_selection(self) -> None:
        self._refresh_all()
        self._update_fields()
        self._refresh_timeline_clips()
        self._refresh_preview()

    def _refresh_timeline_clips(self) -> None:
        c = self.cur_clip
        self.timeline.set_selection((c.start, c.end) if c else None)
        self.timeline.set_others([(x.id, x.start, x.end, x.weak) for x in (self.cur_src.clips if self.cur_src else [])
                                  if x is not c])

    def add_manual_clip(self) -> None:
        src = self.cur_src
        if not src or not src.info:
            return
        t = self.preview.player.position() / 1000.0
        dur = min(src.settings.default_duration, src.info.duration)
        start = max(0.0, min(t, src.info.duration - dur))
        c = Clip(start=start, end=start + dur, label="Handmatig", origin="manual", look=copy.deepcopy(self.project.default_look))
        src.clips.append(c)
        self._sort_clips(); self._fill_table(); self.select_clip(c)
        self._update_src_item(src)

    def duplicate_clip(self) -> None:
        src, c = self.cur_src, self.cur_clip
        if not src or not c or not src.info:
            return
        n = copy.deepcopy(c)
        n.id = Clip().id
        n.origin, n.score, n.weak = "manual", None, False
        n.label = (c.label or "Clip") + " (kopie)"
        d = c.duration
        if c.end + d <= src.info.duration:
            n.start, n.end = c.end, c.end + d
        else:
            n.start, n.end = max(0.0, c.start - d), max(0.0, c.start - d) + d
        src.clips.append(n)
        self._sort_clips(); self._fill_table(); self.select_clip(n)
        self._update_src_item(src)

    def delete_clip(self) -> None:
        src, c = self.cur_src, self.cur_clip
        if not src or not c:
            return
        i = self._row_of(c)
        src.clips.remove(c)
        self._fill_table()
        self.cur_clip = None
        if src.clips:
            self.select_clip(src.clips[min(i, len(src.clips) - 1)])
        else:
            self._after_selection()
        self._update_src_item(src)

    def analyze(self) -> None:
        src = self.cur_src
        if not src or not src.info:
            return
        f = self.feats.get(src.path)
        if f is None:
            if not src.info.has_audio:
                QMessageBox.information(self, "Geen audio", "Deze video heeft geen audiospoor. Maak handmatig clips.")
            elif self.bg is not None:
                QMessageBox.information(self, "Even geduld", "De audio van deze set wordt nog geanalyseerd. "
                                        "Probeer het zo opnieuw; de voortgang staat onderin.")
            else:
                self._enqueue("extract", src)
                self.status_lbl.setText("Audio-analyse gestart. Klik daarna opnieuw op 'Zoek suggesties'.")
            return
        st = src.settings
        avoid = [(c.start, c.end) for c in src.clips if c.origin == "manual"]
        try:
            sugg, note = hl.suggest(f, st.default_duration, st.clip_count, st.mode, avoid)
        except ValueError as e:
            QMessageBox.warning(self, "Analyse", str(e))
            return
        src.clips = [c for c in src.clips if c.origin != "auto"]
        for g in sugg:
            src.clips.append(Clip(start=g.start, end=g.start + g.duration, reason=g.reason, score=g.score, weak=g.weak,
                                  origin="auto", approved=not g.weak, look=copy.deepcopy(self.project.default_look)))
        if any(g.weak for g in sugg):
            note += " Zwakke suggesties staan standaard uitgevinkt."
        src.analysis_note = note
        self.analysis_lbl.setText(note)
        self._sort_clips(); self._fill_table()
        if src.clips:
            self.select_clip(src.clips[0])
        else:
            self.cur_clip = None; self._after_selection()
        self._update_src_item(src)

    # ------------------------------------------------------------------ in/uit-velden
    def _update_fields(self) -> None:
        c = self.cur_clip
        for e in (self.ed_start, self.ed_end, self.ed_dur):
            e.blockSignals(True)
        if c:
            self.ed_start.setText(format_time(c.start, 2, True)); self.ed_end.setText(format_time(c.end, 2, True))
            self.ed_dur.setText(f"{c.duration:.2f}")
        else:
            for e in (self.ed_start, self.ed_end, self.ed_dur):
                e.clear()
        for e in (self.ed_start, self.ed_end, self.ed_dur):
            e.blockSignals(False)

    def apply_field(self, which: str) -> None:
        c, src = self.cur_clip, self.cur_src
        if not c or not src or not src.info:
            return
        total = src.info.duration
        try:
            if which == "start":
                s = parse_time(self.ed_start.text())
                d = c.duration
                c.start = min(max(0.0, s), total - 0.5)
                if c.start > c.end - 0.5:
                    c.end = min(total, c.start + d)
            elif which == "end":
                e = parse_time(self.ed_end.text())
                if e < c.start + 0.5:
                    raise ValueError("Het einde moet minstens 0,5 s na de start liggen.")
                c.end = min(total, e)
            else:
                d = float(self.ed_dur.text().replace(",", "."))
                if d < 0.5:
                    raise ValueError("De duur moet minstens 0,5 s zijn.")
                c.end = min(total, c.start + d)
        except ValueError as e:
            self.status_lbl.setText(f"Ongeldige invoer: {e}")
            self._update_fields()
            return
        self._clip_modified_final()

    def set_point(self, which: str) -> None:
        c, src = self.cur_clip, self.cur_src
        if not c or not src or not src.info:
            return
        t = self.preview.player.position() / 1000.0
        if which == "start":
            if t >= c.end - 0.5:
                self.status_lbl.setText("De start moet vóór het einde liggen.")
                return
            c.start = max(0.0, t)
        else:
            if t <= c.start + 0.5:
                self.status_lbl.setText("Het einde moet ná de start liggen.")
                return
            c.end = min(src.info.duration, t)
        self._clip_modified_final()

    def on_range_edit(self, a: float, b: float) -> None:
        if self.cur_clip:
            self.cur_clip.start, self.cur_clip.end = a, b
            self._update_fields()
            row = self._row_of(self.cur_clip)
            if row >= 0:
                self._filling = True
                self._set_row(row, self.cur_clip)
                self._filling = False

    def _clip_modified_final(self) -> None:
        c = self.cur_clip
        if not c:
            return
        self._sort_clips(); self._fill_table()
        row = self._row_of(c)
        if row >= 0:
            self._filling = True; self.table.selectRow(row); self._filling = False
        self._update_fields(); self._refresh_timeline_clips()

    def on_dur_combo(self) -> None:
        st = self._set()
        if st is None or self._loading:
            return
        d = self.cb_dur.currentData()
        if d == -1:
            st.default_duration = float(self.sp_dur.value())
        else:
            st.default_duration = float(d)
        self._update_enablement()

    def on_dur_custom(self, v: float) -> None:
        st = self._set()
        if st is not None and not self._loading and self.cb_dur.currentData() == -1:
            st.default_duration = float(v)

    # ------------------------------------------------------------------ afspelen
    def on_position(self, ms: int) -> None:
        t = ms / 1000.0
        self.timeline.set_playhead(t)
        total = self.cur_src.info.duration if self.cur_src and self.cur_src.info else 0
        self.time_lbl.setText(f"{format_time(t, 2)} / {format_time(total, 0)}")
        playing = self.preview.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if playing:
            self.timeline.ensure_visible(t)
            c = self.cur_clip
            if c and self.chk_loop.isChecked() and t >= c.end:
                self.preview.player.pause()
                self.seek(c.start)

    def on_state(self, _state) -> None:
        playing = self.preview.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setText("❚❚ Pauzeren" if playing else "▶︎ Afspelen")

    def on_player_error(self, _err, text: str) -> None:
        self.status_lbl.setText(f"Voorbeeld kan dit bestand niet afspelen: {text}. Exporteren kan nog steeds werken.")

    def toggle_play(self) -> None:
        pl = self.preview.player
        if self.cur_src is None:
            return
        if pl.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            pl.pause()
            return
        c = self.cur_clip
        t = pl.position() / 1000.0
        if c and self.chk_loop.isChecked() and not (c.start <= t < c.end - 0.05):
            self.seek(c.start)
        pl.play()

    def seek(self, t: float) -> None:
        total = self.cur_src.info.duration if self.cur_src and self.cur_src.info else 0.0
        t = min(max(0.0, t), max(0.0, total))
        self.preview.player.setPosition(int(round(t * 1000)))
        self.timeline.set_playhead(t)
        self.time_lbl.setText(f"{format_time(t, 2)} / {format_time(total, 0)}")

    def step_frames(self, n: int) -> None:
        if not self.cur_src or not self.cur_src.info:
            return
        self.preview.player.pause()
        fps = self.cur_src.info.fps or 25.0
        self.seek(self.preview.player.position() / 1000.0 + n / fps)

    def keyPressEvent(self, ev) -> None:
        k, shift = ev.key(), bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if k == Qt.Key.Key_Space:
            self.toggle_play()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            sign = -1 if k == Qt.Key.Key_Left else 1
            if shift:
                self.seek(self.preview.player.position() / 1000.0 + sign)
            else:
                self.step_frames(sign)
        elif k == Qt.Key.Key_I:
            self.set_point("start")
        elif k == Qt.Key.Key_O:
            self.set_point("end")
        else:
            super().keyPressEvent(ev)

    # ------------------------------------------------------------------ preview-koppeling
    def _text_png(self, st: ExportSettings) -> str | None:
        if not st.dj_name.strip():
            return None
        key = text_key(st)
        if key not in self._text_cache:
            if len(self._text_cache) > 40:
                self._text_cache.clear()
            self._text_cache[key] = render_text_png(st, self._tmp)
        return self._text_cache[key]

    def _refresh_preview(self) -> None:
        src = self.cur_src
        if not src or not src.info:
            self.preview.update_scene(None, None, None, None)
            return
        look = self.cur_clip.look if self.cur_clip else self.project.default_look
        self.preview.update_scene(src.info, look, src.settings, self._text_png(src.settings))

    def _text_changed(self) -> None:
        self._refresh_preview()

    def on_crop_drag(self, fx: float, fy: float) -> None:
        if self.cur_clip:
            self.cur_clip.look.fx, self.cur_clip.look.fy = fx, fy
            self.refresh_binds()
            self._refresh_preview()

    def on_logo_move(self, cx: float, cy: float) -> None:
        if self.cur_src:
            self.cur_src.settings.logo_cx, self.cur_src.settings.logo_cy = cx, cy
            self.refresh_binds(); self._refresh_preview()

    def on_text_move(self, cx: float, cy: float) -> None:
        if self.cur_src:
            self.cur_src.settings.dj_cx, self.cur_src.settings.dj_cy = cx, cy
            self.refresh_binds(); self._refresh_preview()

    def center_crop(self) -> None:
        if self.cur_clip:
            self.cur_clip.look.fx = self.cur_clip.look.fy = 0.5
            self.cur_clip.look.zoom = 1.0
            self.refresh_binds(); self._refresh_preview()

    def neutral_color(self) -> None:
        if self.cur_clip:
            lk = self.cur_clip.look
            lk.brightness, lk.contrast, lk.saturation = 0.0, 1.0, 1.0
            self.refresh_binds()

    def _refresh_all(self) -> None:
        self.refresh_binds()
        st = self._set()
        self._loading = True
        if st is not None:
            i = self.cb_profile.findData(st.profile)
            self.cb_profile.setCurrentIndex(i if i >= 0 else 0)
            self.ed_logo.setText(st.logo_path)
            self.btn_dcol.setStyleSheet(f"background-color: {st.dj_color}; color: #888; border: 1px solid #666;")
            self.btn_wcol.setStyleSheet(f"background-color: {st.wave_color}; color: #222; border: 1px solid #666;")
            self.ed_caption.blockSignals(True)
            self.ed_caption.setPlainText(st.caption_template)
            self.ed_caption.blockSignals(False)
            d = st.default_duration
            i = self.cb_dur.findData(int(d)) if float(d).is_integer() else -1
            self.cb_dur.setCurrentIndex(i if i >= 0 else self.cb_dur.findData(-1))
            self.sp_dur.setValue(d)
        self._loading = False
        self._update_profile_help()
        self._update_info()
        self._update_enablement()

    def _update_profile_help(self) -> None:
        st = self._set()
        if st is not None:
            self.profile_help.setText(QUALITY_PROFILES.get(st.profile, QUALITY_PROFILES["custom"])["help"])

    def on_profile(self) -> None:
        st = self._set()
        if st is None or self._loading:
            return
        apply_profile(st, self.cb_profile.currentData())
        self.refresh_binds()
        self._update_profile_help(); self._update_enablement()

    def _adv_changed(self) -> None:
        st = self._set()
        if st is None:
            return
        if st.profile != "custom":
            st.profile = "custom"
            st.lossless = False
            self._loading = True
            self.cb_profile.setCurrentIndex(self.cb_profile.findData("custom"))
            self._loading = False
        self._update_profile_help(); self._update_enablement()

    def _update_enablement(self) -> None:
        src, clip = self.cur_src, self.cur_clip
        has_src = src is not None and src.info is not None
        has_clip = clip is not None
        st = src.settings if src else None
        busy = self.export_worker is not None
        for w in (self.cb_crop, self.sp_bri, self.sp_con, self.sp_sat, self.btn_neutral, self.sp_vfi, self.sp_vfo,
                  self.sp_gain, self.chk_mute, self.sp_afi, self.sp_afo, self.btn_exact, self.btn_apply_look,
                  self.btn_apply_look_all, self.btn_look_default, self.btn_dup, self.btn_del, self.btn_set_in,
                  self.btn_set_out, self.ed_start, self.ed_end, self.ed_dur):
            w.setEnabled(has_clip)
        audio_only = has_src and src.info.audio_only
        fill = has_clip and clip.look.crop_mode == "fill" and not audio_only
        for w in (self.sp_zoom, self.sp_fx, self.sp_fy, self.btn_center):
            w.setEnabled(fill)
        self.cb_crop.setEnabled(has_clip and not audio_only)
        self.cb_avis.setEnabled(audio_only); self.btn_wcol.setEnabled(audio_only)
        self.btn_tracks.setEnabled(has_src)
        self.btn_link.setEnabled(bool(self.ffmpeg))
        for w in (self.chk_cover, self.chk_caption):
            w.setEnabled(has_src)
        self.ed_caption.setEnabled(has_src and st.caption_enabled)
        for w in (self.cb_mode, self.cb_dur, self.sp_count, self.btn_analyze, self.btn_add_clip, self.cb_profile,
                  self.cb_enc, self.cb_fps, self.cb_abr, self.cb_aenc, self.cb_norm, self.btn_logo, self.btn_logo_rm,
                  self.sp_lw, self.sp_lo, self.sp_lx, self.sp_ly, self.ed_dj, self.sp_dsz, self.chk_dout,
                  self.chk_dbold, self.btn_dcol, self.sp_dop, self.sp_dx, self.sp_dy, self.btn_apply_set):
            w.setEnabled(has_src)
        self.btn_analyze.setEnabled(has_src and bool(self.ffmpeg))
        vt = st is not None and st.encoder == "h264_videotoolbox"
        x264 = st is not None and not vt and not st.lossless
        self.sp_crf.setEnabled(has_src and x264); self.cb_preset.setEnabled(has_src and not vt)
        self.sp_vtq.setEnabled(has_src and vt)
        self.cb_hdr.setEnabled(has_src and bool(src.info.hdr))
        self.sp_lufs.setEnabled(has_src and st.normalize == "loudness")
        self.sp_peak.setEnabled(has_src and st.normalize == "peak")
        self.sp_dur.setEnabled(has_src and self.cb_dur.currentData() == -1)
        self.btn_export.setEnabled(has_src and bool(self.ffmpeg) and not busy)
        self.btn_export_all.setEnabled(bool(self.project.sources) and bool(self.ffmpeg) and not busy)

    # ------------------------------------------------------------------ branding en "toepassen op alles"
    def choose_logo(self) -> None:
        st = self._set()
        if st is None:
            return
        start = str(self.settings_store.value("last_logo_dir", str(Path.home() / "Pictures")))
        fn, _ = QFileDialog.getOpenFileName(self, "Kies het ACCU-logo", start,
                                            "Afbeeldingen (*.png *.jpg *.jpeg *.webp *.tif *.tiff);;Alle bestanden (*)")
        if not fn:
            return
        if QPixmap(fn).isNull():
            QMessageBox.warning(self, "Logo", "Dit bestand kon niet als afbeelding worden gelezen.")
            return
        self.settings_store.setValue("last_logo_dir", os.path.dirname(fn))
        st.logo_path = fn
        self.ed_logo.setText(fn); self._refresh_preview()

    def remove_logo(self) -> None:
        st = self._set()
        if st is not None:
            st.logo_path = ""
            self.ed_logo.setText(""); self._refresh_preview()

    def choose_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog
        st = self._set()
        if st is None:
            return
        col = QColorDialog.getColor(QColor(st.dj_color), self, "Tekstkleur")
        if col.isValid():
            st.dj_color = col.name()
            self.btn_dcol.setStyleSheet(f"background-color: {st.dj_color}; color: #888; border: 1px solid #666;")
            self._refresh_preview()

    def apply_look(self, everywhere: bool) -> None:
        if not self.cur_clip or not self.cur_src:
            return
        targets = [c for s in (self.project.sources if everywhere else [self.cur_src]) for c in s.clips]
        if everywhere and QMessageBox.question(self, "Toepassen", f"Instellingen van deze clip op {len(targets)} clips "
                                               "van alle sets toepassen?") != QMessageBox.StandardButton.Yes:
            return
        for c in targets:
            if c is not self.cur_clip:
                c.look = copy.deepcopy(self.cur_clip.look)
        self.status_lbl.setText(f"Instellingen toegepast op {len(targets)} clips.")

    def look_as_default(self) -> None:
        if self.cur_clip:
            self.project.default_look = copy.deepcopy(self.cur_clip.look)
            self.status_lbl.setText("Dit wordt de standaard voor nieuwe clips.")

    def apply_set_all(self) -> None:
        st = self._set()
        if st is None:
            return
        others = [s for s in self.project.sources if s is not self.cur_src]
        if QMessageBox.question(self, "Toepassen", f"Kwaliteit, audio, logo en modus toepassen op {len(others)} andere set(s)? "
                                "De DJ-naam blijft per set behouden.") != QMessageBox.StandardButton.Yes:
            return
        for s in others:
            keep = s.settings.dj_name
            s.settings = copy.deepcopy(st)
            s.settings.dj_name = keep
        self.project.defaults = copy.deepcopy(st)
        self.project.defaults.dj_name = ""
        self.status_lbl.setText("Instellingen toegepast; nieuwe sets gebruiken deze ook.")

    def show_exact(self) -> None:
        src, clip = self.cur_src, self.cur_clip
        if not src or not clip or not src.info or not self.ffmpeg:
            return
        st, info, look = src.settings, src.info, copy.deepcopy(clip.look)
        t = min(max(self.preview.player.position() / 1000.0, 0.0), max(0.0, info.duration - 0.1))
        png_text = self._text_png(st)
        out = os.path.join(self._tmp, "exact.png")
        if os.path.exists(out):
            os.remove(out)

        def job(w: Worker):
            td = tempfile.mkdtemp(dir=self._tmp)
            ovs = ex.make_overlays(st, td, lambda s, d: png_text)
            cmd = ex.build_still_command(self.ffmpeg, info, t, look, st, ovs, out)
            from ..ffmpeg_tools import run_capture
            cp = run_capture(cmd, timeout=180)
            if cp.returncode != 0 or not os.path.isfile(out):
                raise ToolError(ex.friendly_error(cp.stderr[-800:], cp.returncode))
            return out

        def done(path):
            dlg = QDialog(self)
            dlg.setWindowTitle(f"Exact exportframe op {format_time(t, 2)} ({OUT_W}×{OUT_H})")
            lay = QVBoxLayout(dlg)
            lb = QLabel()
            pm = QPixmap(path)
            h = max(400, min(900, int((self.screen().availableGeometry().height() if self.screen() else 900) * 0.85)))
            lb.setPixmap(pm.scaledToHeight(h, Qt.TransformationMode.SmoothTransformation))
            lay.addWidget(lb)
            lay.addWidget(hint("Dit frame is door FFmpeg gemaakt met dezelfde filterketen als de export (excl. fades)."))
            dlg.exec()
        self._start_worker(Worker(job), done, lambda m: QMessageBox.warning(self, "Exportframe", m),
                           "Exportframe maken…", track_bg=False)

    # ------------------------------------------------------------------ export
    def choose_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Kies de exportmap", self.project.out_dir or str(videos_dir()))
        if d:
            self.project.out_dir = d
            self.ed_out.setText(d)
            self.settings_store.setValue("out_dir", d)

    def open_out_dir(self) -> None:
        d = self.project.out_dir
        os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def start_export(self, sources: list[Source]) -> None:
        if self.export_worker is not None or not sources:
            return
        if not self.ffmpeg:
            QMessageBox.critical(self, "FFmpeg niet gevonden", self.tool_error)
            return
        jobs: list[tuple[Source, Clip, int]] = []
        problems: list[str] = []
        for src in sources:
            if not src.info:
                continue
            clips = sorted(src.clips, key=lambda c: c.start)
            for i, c in enumerate(clips, start=1):
                if c.approved:
                    jobs.append((src, c, i))
            if not os.path.isfile(src.path):
                problems.append(f"Bronbestand niet beschikbaar: {src.path}")
            if src.info.hdr and src.settings.hdr_mode == "tonemap" and not self.has_zscale:
                problems.append(f"{src.name}: HDR-omzetting vereist FFmpeg met 'zscale' (de gevonden FFmpeg heeft dat niet).")
            if src.settings.encoder == "h264_videotoolbox" and not self.has_vt and not src.settings.lossless:
                problems.append(f"{src.name}: VideoToolbox is niet beschikbaar in deze FFmpeg; kies een software-profiel.")
        if not jobs:
            QMessageBox.information(self, "Niets te exporteren", "Er zijn geen goedgekeurde clips (vinkje in de lijst).")
            return
        if problems:
            QMessageBox.warning(self, "Export niet mogelijk", "\n".join(problems))
            return
        out_dir = self.project.out_dir
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Exportmap", f"De exportmap kan niet worden gemaakt:\n{e}")
            return
        for s in {id(j[0]): j[0] for j in jobs}.values():
            self._text_png(s.settings)       # tekstafbeeldingen op de hoofdthread voorbereiden
        texts = dict(self._text_cache)
        label = {"t": ""}

        def job(w: Worker):
            def on_start(n, total, s, c):
                label["t"] = f"Exporteren {n + 1}/{total}: {s.name} · {format_time(c.start, 0)}"
                w.report(n / total, label["t"])
            res = ex.run_queue(self.ffmpeg, jobs, out_dir, on_start=on_start,
                               on_progress=lambda n, total, f: w.report((n + f) / total, label["t"]),
                               cancel=w.cancel_event, text_renderer=lambda st, d: texts.get(text_key(st)))
            try:
                summary = ex.write_summary(out_dir, jobs, res)
            except OSError:
                summary = ""
            return res, summary
        worker = Worker(job)
        self.export_worker = worker
        self.log.appendPlainText(f"— Export gestart: {len(jobs)} clip(s) naar {out_dir}")
        self._workers.add(worker)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        worker.progress.connect(lambda f, t, wk=worker: self._on_progress(wk, f, t))
        worker.done.connect(self._export_done)
        worker.failed.connect(lambda m: self._export_end(f"Export mislukt: {m}"))
        worker.cancelled.connect(lambda: self._export_end("Export geannuleerd."))
        self.pbar.setValue(0); self.pbar.show(); self.btn_cancel.show()
        self._update_enablement()
        worker.start()

    def _export_end(self, text: str) -> None:
        self.export_worker = None
        self.log.appendPlainText(text)
        self.status_lbl.setText(text)
        self.pbar.hide(); self.btn_cancel.hide()
        self._update_enablement()
        if self.bg_queue:
            self._pump()

    def _export_done(self, payload) -> None:
        results, summary = payload
        ok = [r for r in results if r.ok]
        bad = [r for r in results if not r.ok and not r.cancelled]
        canc = [r for r in results if r.cancelled]
        for r in results:
            if r.ok:
                self.log.appendPlainText(f"✓ {os.path.basename(r.path)}")
            elif r.cancelled:
                self.log.appendPlainText(f"– geannuleerd: {r.source} @ {format_time(r.start, 0)}")
            else:
                self.log.appendPlainText(f"✗ {r.source} @ {format_time(r.start, 0)}: {r.error}")
        if summary:
            self.log.appendPlainText(f"Overzicht: {summary}")
        self._export_end(f"Export klaar: {len(ok)} gelukt, {len(bad)} mislukt, {len(canc)} geannuleerd.")
        box = QMessageBox(self)
        box.setWindowTitle("Export klaar")
        text = f"{len(ok)} clip(s) geëxporteerd naar:\n{self.project.out_dir}"
        if bad:
            text += f"\n\n{len(bad)} mislukt (de rest is wel gemaakt):\n" + "\n".join(
                f"• {r.source} @ {format_time(r.start, 0)}: {r.error.splitlines()[0] if r.error else '?'}" for r in bad[:5])
        if canc:
            text += f"\n{len(canc)} geannuleerd."
        box.setText(text)
        opener = box.addButton(f"Toon in {file_manager_name()}", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Sluiten", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is opener:
            self.open_out_dir()

    # ------------------------------------------------------------------ project
    def save_project(self, save_as: bool) -> None:
        path = self.project_path
        if save_as or not path:
            start = str(self.settings_store.value("last_proj_dir", str(Path.home() / "Documents")))
            path, _ = QFileDialog.getSaveFileName(self, "Project opslaan", os.path.join(start, "DJ-sets" + PROJECT_EXT),
                                                  f"Radio ACCU-project (*{PROJECT_EXT})")
            if not path:
                return
            if not path.endswith(PROJECT_EXT):
                path += PROJECT_EXT
        try:
            self.project.save(path)
        except OSError as e:
            QMessageBox.warning(self, "Opslaan mislukt", str(e))
            return
        self.project_path = path
        self.settings_store.setValue("last_proj_dir", os.path.dirname(path))
        self.setWindowTitle(f"{APP_NAME} – {os.path.basename(path)}")
        self.status_lbl.setText(f"Project opgeslagen: {path}")

    def open_project(self) -> None:
        start = str(self.settings_store.value("last_proj_dir", str(Path.home() / "Documents")))
        path, _ = QFileDialog.getOpenFileName(self, "Project openen", start, f"Radio ACCU-project (*{PROJECT_EXT});;Alle bestanden (*)")
        if path:
            self.load_project(path)

    def load_project(self, path: str) -> None:
        try:
            proj = Project.load(path)
        except (OSError, ValueError, KeyError) as e:
            QMessageBox.warning(self, "Openen mislukt", f"Dit projectbestand kon niet worden gelezen:\n{e}")
            return
        if self.export_worker is not None:
            QMessageBox.information(self, "Even wachten", "Er loopt nog een export.")
            return
        if self.bg is not None:
            self.bg_queue.clear(); self.bg.cancel()
        self.cur_src = self.cur_clip = None
        proj.sources = [x for x in proj.sources if x.path]     # onafgemaakte downloads niet terugzetten
        self.project = proj
        if not proj.out_dir:
            proj.out_dir = str(self.settings_store.value("out_dir", DEFAULT_OUT))
        self.ed_out.setText(proj.out_dir)
        self.project_path = path if path.endswith(PROJECT_EXT) else None
        self.feats.clear()
        self.src_list.blockSignals(True)
        self.src_list.clear()
        for s in proj.sources:
            self.src_list.addItem(QListWidgetItem(s.name))
            if s.info:
                self._update_src_item(s)
        self.src_list.blockSignals(False)
        for s in proj.sources:
            if s.info is None:
                if os.path.isfile(s.path):
                    self._enqueue("probe", s)
            elif s.info.has_audio and os.path.isfile(s.path):
                c = fe.load_cached(s.path)
                if c is not None:
                    self.feats[s.path] = c
                else:
                    self._enqueue("extract", s)
        self.setWindowTitle(f"{APP_NAME} – {os.path.basename(path)}")
        if proj.sources:
            self.src_list.setCurrentRow(0)
            self.on_select_source(0)
        else:
            self.on_select_source(-1)
        missing = [s.name for s in proj.sources if not os.path.isfile(s.path)]
        if missing:
            QMessageBox.warning(self, "Bestanden ontbreken", "Deze bronvideo's zijn niet gevonden (schijf aangesloten?):\n"
                                + "\n".join(missing))

    def _session_file(self) -> Path:
        return SUPPORT_DIR / "last_session.accuclip"

    def _offer_restore(self) -> None:
        f = self._session_file()
        if self.project.sources or not f.is_file():
            return
        try:
            n = len(Project.load(str(f)).sources)
        except Exception:
            return
        if n and QMessageBox.question(self, "Vorige sessie", f"Je vorige sessie ({n} set(s)) herstellen?") == QMessageBox.StandardButton.Yes:
            self.load_project(str(f))
            self.project_path = None
            self.setWindowTitle(APP_NAME)

    # ------------------------------------------------------------------ slepen en afsluiten
    def open_paths(self, paths: list[str]) -> None:
        """Projectbestand → openen; video's/audio → toevoegen."""
        projects = [p for p in paths if p.lower().endswith(PROJECT_EXT)]
        if projects:
            self.load_project(projects[0])
        others = [p for p in paths if not p.lower().endswith(PROJECT_EXT)]
        if others:
            self.add_files(others)

    def dragEnterEvent(self, ev) -> None:
        md = ev.mimeData()
        if md.hasUrls() or (md.hasText() and dl.extract_urls(md.text())):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        md = ev.mimeData()
        web = [u.toString() for u in md.urls() if not u.isLocalFile() and u.scheme() in ("http", "https")]
        if not md.urls() and md.hasText():
            web = dl.extract_urls(md.text())
        if web:
            self.add_link_dialog("\n".join(web))
            return
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        if paths:
            self.open_paths(paths)

    def closeEvent(self, ev) -> None:
        if self.export_worker is not None:
            if QMessageBox.question(self, "Export loopt nog", "Er wordt nog geëxporteerd. Annuleren en afsluiten?") \
                    != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self.export_worker.cancel()
        if self.bg is not None:
            self.bg.cancel()
        self.bg_queue.clear()
        self.preview.player.stop()
        for w in list(self._workers):
            w.wait(8000)
        try:
            SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
            self.project.save(str(self._session_file()))
        except OSError:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)
        ev.accept()
