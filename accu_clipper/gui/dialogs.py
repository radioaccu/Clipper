"""Dialoogvensters: link toevoegen en tracklist/hoofdstukken omzetten naar clips."""
from __future__ import annotations
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from .. import downloader as dl
from ..platform_paths import IS_MAC, videos_dir
from ..timecode import format_time, parse_time
from ..tracklist import parse_tracklist, tracks_to_ranges


def _hint(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet("color: #8a8f98; font-size: 11px;")
    return lb


class LinkDialog(QDialog):
    """Vraagt een of meer links en de downloadopties. Downloaden zelf gebeurt in het hoofdvenster."""

    def __init__(self, parent, prefill: str, dl_dir: str, tool_status: str, last: dict):
        super().__init__(parent)
        self.setWindowTitle("Link toevoegen")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Plak een of meer links (één per regel). YouTube, SoundCloud, Mixcloud, Vimeo, Twitch, "
                             "Dropbox, directe links naar video- of audiobestanden en honderden andere sites."))
        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.urls.setPlainText(prefill)
        self.urls.setFixedHeight(90)
        lay.addWidget(self.urls)
        f = QFormLayout()
        self.quality = QComboBox()
        for k, (label, _) in dl.QUALITIES.items():
            self.quality.addItem(label, k)
        self.cookies = QComboBox()
        for k, label in dl.BROWSERS.items():
            self.cookies.addItem(label, k)
        for combo, key in ((self.quality, "quality"), (self.cookies, "cookies")):
            i = combo.findData(last.get(key))
            if i >= 0:
                combo.setCurrentIndex(i)
        f.addRow("Kwaliteit", self.quality)
        f.addRow("Cookies uit browser", self.cookies)
        f.addRow(_hint("Cookies zijn alleen nodig als een site om inloggen vraagt (bijv. 'bevestig dat je geen bot "
                       "bent'). Ze worden direct uit je browser gelezen en nergens anders heen gestuurd."
                       + ("" if IS_MAC else " Op Windows werkt Firefox het betrouwbaarst; sluit Chrome/Edge eerst af.")))
        self.chk_section = QCheckBox("Alleen een fragment downloaden")
        self.sec_from, self.sec_to = QLineEdit(), QLineEdit()
        self.sec_from.setPlaceholderText("van, bijv. 1:00:00")
        self.sec_to.setPlaceholderText("tot, bijv. 1:30:00")
        row = QHBoxLayout(); row.addWidget(self.sec_from); row.addWidget(QLabel("–")); row.addWidget(self.sec_to)
        f.addRow(self.chk_section)
        f.addRow("Fragment", row)
        f.addRow(_hint("Een fragment wordt op keyframes geknipt (een paar seconden speling). Tijden in de app gaan "
                       "dan vanaf het begin van het fragment, en hoofdstukken/tracklist kloppen niet meer."))
        self.chk_tracks = QCheckBox("Tracklist uit de beschrijving of hoofdstukken direct als clips toevoegen")
        self.chk_tracks.setChecked(bool(last.get("tracks", False)))
        f.addRow(self.chk_tracks)
        self.dir_edit = QLineEdit(dl_dir); self.dir_edit.setReadOnly(True)
        b = QPushButton("Kies…"); b.clicked.connect(self._choose_dir)
        row2 = QHBoxLayout(); row2.addWidget(self.dir_edit, 1); row2.addWidget(b)
        f.addRow("Downloadmap", row2)
        lay.addLayout(f)
        self.status = _hint(tool_status)
        lay.addWidget(self.status)
        lay.addWidget(_hint("Download alleen materiaal waarvoor je de rechten of toestemming hebt."))
        self.error = QLabel(""); self.error.setStyleSheet("color: #e05050;"); self.error.setWordWrap(True)
        lay.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Downloaden")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuleren")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.chk_section.toggled.connect(self._sync)
        self._sync()
        self.result_opts: dict = {}

    def _sync(self) -> None:
        on = self.chk_section.isChecked()
        self.sec_from.setEnabled(on)
        self.sec_to.setEnabled(on)

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Kies de downloadmap", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _accept(self) -> None:
        urls = dl.extract_urls(self.urls.toPlainText())
        if not urls:
            self.error.setText("Geen geldige link gevonden. Een link begint met http:// of https://.")
            return
        section = None
        if self.chk_section.isChecked():
            try:
                a, b = parse_time(self.sec_from.text()), parse_time(self.sec_to.text())
            except ValueError as e:
                self.error.setText(f"Fragmenttijd ongeldig: {e}")
                return
            if b <= a:
                self.error.setText("Het einde van het fragment moet na het begin liggen.")
                return
            section = (a, b)
        self.result_opts = dict(urls=urls, quality=self.quality.currentData(), cookies=self.cookies.currentData(),
                                section=section, tracks=self.chk_tracks.isChecked(), dir=self.dir_edit.text())
        self.accept()


class TracklistDialog(QDialog):
    """Plak een tracklist (of gebruik de hoofdstukken) en maak er clips van."""

    def __init__(self, parent, text: str, total: float, default_duration: float):
        super().__init__(parent)
        self.setWindowTitle("Clips uit tracklist of hoofdstukken")
        self.setMinimumSize(560, 520)
        self.total = total
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Plak de tracklist met tijden (bijv. uit de YouTube-beschrijving). Elke regel met een "
                             "tijd wordt een clip; regels zonder tijd worden overgeslagen."))
        self.text = QPlainTextEdit(); self.text.setPlainText(text)
        self.text.setPlaceholderText("00:00 Intro\n03:12 Artiest – Titel\n1:02:03 Artiest – Titel")
        lay.addWidget(self.text, 1)
        f = QFormLayout()
        self.offset = QDoubleSpinBox(); self.offset.setRange(-60, 600); self.offset.setSuffix(" s")
        self.offset.setDecimals(1); self.offset.setValue(0.0)
        self.duration = QDoubleSpinBox(); self.duration.setRange(5, 600); self.duration.setSuffix(" s")
        self.duration.setDecimals(1); self.duration.setValue(default_duration)
        self.stop = QCheckBox("Clip stopt uiterlijk waar de volgende track begint"); self.stop.setChecked(True)
        self.replace = QCheckBox("Eerder uit een tracklist gemaakte clips vervangen"); self.replace.setChecked(True)
        f.addRow("Start na trackbegin", self.offset)
        f.addRow(_hint("Bij DJ-sets loopt de vorige track vaak nog even door. Met bijv. 30 s begint de clip pas "
                       "als de nieuwe track goed hoorbaar is."))
        f.addRow("Clipduur", self.duration)
        f.addRow(self.stop); f.addRow(self.replace)
        lay.addLayout(f)
        self.preview = _hint("")
        lay.addWidget(self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Clips maken")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuleren")
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.offset, self.duration):
            w.valueChanged.connect(self._update)
        self.stop.toggled.connect(self._update)
        self.text.textChanged.connect(self._update)
        self.ranges: list[tuple[float, float, str]] = []
        self._update()

    def _update(self) -> None:
        tracks = parse_tracklist(self.text.toPlainText(), self.total)
        self.ranges = tracks_to_ranges(tracks, self.duration.value(), self.offset.value(), self.total,
                                       self.stop.isChecked())
        if not tracks:
            self.preview.setText("Nog geen tijden herkend.")
        else:
            shown = ", ".join(f"{format_time(a)} {t[:30]}" for a, _, t in self.ranges[:6])
            more = f" … (+{len(self.ranges) - 6})" if len(self.ranges) > 6 else ""
            skipped = len(tracks) - len(self.ranges)
            self.preview.setText(f"{len(tracks)} tracks herkend → {len(self.ranges)} clips: {shown}{more}"
                                 + (f"\n{skipped} overgeslagen (te kort na de startverschuiving)." if skipped else ""))
        self.ok_btn.setEnabled(bool(self.ranges))


def chapters_as_text(chapters: list[dict]) -> str:
    return "\n".join(f"{format_time(float(c['start']), 0, True)} {c.get('title') or 'Hoofdstuk'}" for c in chapters)


def default_download_dir() -> str:
    return str(videos_dir() / "Radio ACCU Downloads")
