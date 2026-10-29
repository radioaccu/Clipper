"""Tijdlijn met waveform, afspeelkop en sleepbare in-/uitpunten."""
from __future__ import annotations
import numpy as np

from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QScrollBar, QHBoxLayout, QPushButton

from ..features import FPS
from ..timecode import format_time

HANDLE_PX = 7
RULER_H = 20


class _Canvas(QWidget):
    seekRequested = Signal(float)
    rangeEdited = Signal(float, float)       # live tijdens slepen
    rangeFinished = Signal()
    clipClicked = Signal(str)
    viewChanged = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(130)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.duration = 1.0
        self.peaks: np.ndarray | None = None
        self.t0, self.t1 = 0.0, 1.0
        self.playhead = 0.0
        self.sel: tuple[float, float] | None = None
        self.others: list[tuple[str, float, float, bool]] = []   # id, start, end, weak
        self._drag = None
        self._wf_cache: tuple | None = None

    # -------------------------------------------------------------- model
    def set_media(self, duration: float, peaks: np.ndarray | None) -> None:
        self.duration = max(1.0, duration)
        self.peaks = peaks
        self.t0, self.t1 = 0.0, self.duration
        self._wf_cache = None
        self.viewChanged.emit(self.t0, self.t1)
        self.update()

    def set_peaks(self, peaks: np.ndarray | None) -> None:
        self.peaks = peaks
        self._wf_cache = None
        self.update()

    def set_view(self, t0: float, t1: float) -> None:
        span = max(1.0, min(t1 - t0, self.duration))
        t0 = min(max(0.0, t0), self.duration - span)
        self.t0, self.t1 = t0, t0 + span
        self._wf_cache = None
        self.viewChanged.emit(self.t0, self.t1)
        self.update()

    def ensure_visible(self, t: float) -> None:
        if t < self.t0 or t > self.t1:
            span = self.t1 - self.t0
            self.set_view(t - span * 0.2, t - span * 0.2 + span)

    def x_of(self, t: float) -> float:
        return (t - self.t0) / (self.t1 - self.t0) * self.width()

    def t_of(self, x: float) -> float:
        return self.t0 + x / max(1, self.width()) * (self.t1 - self.t0)

    # -------------------------------------------------------------- tekenen
    def _waveform(self, w: int, h: int) -> QPixmap:
        key = (round(self.t0, 3), round(self.t1, 3), w, h, id(self.peaks))
        if self._wf_cache and self._wf_cache[0] == key:
            return self._wf_cache[1]
        pm = QPixmap(w, h)
        pm.fill(QColor(24, 26, 30))
        p = QPainter(pm)
        mid = (h + RULER_H) / 2
        if self.peaks is not None and len(self.peaks) > 1:
            i0 = max(0, int(self.t0 * FPS))
            i1 = min(len(self.peaks), int(self.t1 * FPS) + 2)
            seg = self.peaks[i0:i1]
            if len(seg) >= 1:
                if len(seg) >= w:
                    edges = np.linspace(0, len(seg), w + 1).astype(int)[:-1]
                    cols = np.maximum.reduceat(seg, edges)
                else:
                    xs = np.linspace(0, len(seg) - 1, w)
                    cols = np.interp(xs, np.arange(len(seg)), seg)
                cols = np.clip(cols, 0, 1) ** 0.6
                amp = (h - RULER_H) / 2 - 3
                p.setPen(QPen(QColor(90, 170, 255)))
                for x in range(min(w, len(cols))):
                    a = float(cols[x]) * amp
                    p.drawLine(x, int(mid - a), x, int(mid + a))
        else:
            p.setPen(QColor(120, 120, 120))
            p.drawText(QRectF(0, RULER_H, w, h - RULER_H), Qt.AlignmentFlag.AlignCenter,
                       "Waveform niet beschikbaar (audio wordt geanalyseerd of ontbreekt)")
        p.end()
        self._wf_cache = (key, pm)
        return pm

    def _ticks(self) -> tuple[float, int]:
        span = self.t1 - self.t0
        target = span / max(1, self.width() / 90)
        for step in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
            if step >= target:
                return float(step), 1 if step < 1 else 0
        return 3600.0, 0

    def paintEvent(self, ev) -> None:
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._waveform(w, h))
        # andere clips
        for cid, a, b, weak in self.others:
            xa, xb = self.x_of(a), self.x_of(b)
            if xb < 0 or xa > w:
                continue
            col = QColor(230, 170, 60, 55) if weak else QColor(120, 220, 140, 55)
            p.fillRect(QRectF(xa, RULER_H, max(2.0, xb - xa), h - RULER_H), col)
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 160)))
            p.drawRect(QRectF(xa, RULER_H, max(2.0, xb - xa), h - RULER_H - 1))
        # geselecteerde clip
        if self.sel:
            xa, xb = self.x_of(self.sel[0]), self.x_of(self.sel[1])
            p.fillRect(QRectF(xa, RULER_H, xb - xa, h - RULER_H), QColor(255, 255, 255, 40))
            pen = QPen(QColor(255, 210, 0)); pen.setWidth(2)
            p.setPen(pen)
            p.drawLine(int(xa), RULER_H, int(xa), h)
            p.drawLine(int(xb), RULER_H, int(xb), h)
            p.fillRect(QRectF(xa - 1, RULER_H, 9, 12), QColor(255, 210, 0))
            p.fillRect(QRectF(xb - 8, RULER_H, 9, 12), QColor(255, 210, 0))
        # liniaal
        p.fillRect(0, 0, w, RULER_H, QColor(40, 43, 50))
        step, dec = self._ticks()
        p.setPen(QColor(200, 200, 200))
        f = QFont(); f.setPixelSize(10); p.setFont(f)
        t = (int(self.t0 / step)) * step
        while t <= self.t1 + step:
            x = self.x_of(t)
            if 0 <= x <= w:
                p.drawLine(int(x), RULER_H - 6, int(x), RULER_H)
                p.drawText(int(x) + 3, RULER_H - 7, format_time(t, dec))
            t += step
        # afspeelkop
        x = self.x_of(self.playhead)
        if 0 <= x <= w:
            p.setPen(QPen(QColor(255, 70, 70), 2))
            p.drawLine(int(x), 0, int(x), h)
        p.end()

    # -------------------------------------------------------------- muis
    def _hit_handle(self, x: float) -> str | None:
        if not self.sel:
            return None
        xa, xb = self.x_of(self.sel[0]), self.x_of(self.sel[1])
        if abs(x - xa) <= HANDLE_PX:
            return "start"
        if abs(x - xb) <= HANDLE_PX:
            return "end"
        return None

    def mouseMoveEvent(self, ev) -> None:
        x = ev.position().x()
        if self._drag is None:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._hit_handle(x) else Qt.CursorShape.ArrowCursor)
            return
        kind, t_press, orig, moved = self._drag
        t = min(max(0.0, self.t_of(x)), self.duration)
        if kind == "scrub":
            self.seekRequested.emit(t)
            return
        if abs(x - self._press_x) > 3:
            moved = True
            self._drag = (kind, t_press, orig, True)
        if not moved or not self.sel:
            return
        a, b = orig
        if kind == "start":
            a = min(t, b - 0.5)
        elif kind == "end":
            b = max(t, a + 0.5)
        elif kind == "body":
            d = t - t_press
            d = max(-a, min(self.duration - b, d))
            a, b = a + d, b + d
        a, b = max(0.0, a), min(self.duration, b)
        self.sel = (a, b)
        self.rangeEdited.emit(a, b)
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        x, y = ev.position().x(), ev.position().y()
        self._press_x = x
        t = min(max(0.0, self.t_of(x)), self.duration)
        if y < RULER_H:
            self._drag = ("scrub", t, None, True)
            self.seekRequested.emit(t)
            return
        h = self._hit_handle(x)
        if h and self.sel:
            self._drag = (h, t, self.sel, False)
            return
        if self.sel and self.sel[0] < t < self.sel[1]:
            self._drag = ("body", t, self.sel, False)
            return
        for cid, a, b, _ in self.others:
            if a <= t <= b:
                self.clipClicked.emit(cid)
                return
        self._drag = ("click", t, None, False)

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag is None:
            return
        kind, t_press, orig, moved = self._drag
        self._drag = None
        if kind in ("start", "end", "body"):
            if moved:
                self.rangeFinished.emit()
            else:
                self.seekRequested.emit(t_press)
        elif kind == "click":
            self.seekRequested.emit(t_press)

    def wheelEvent(self, ev) -> None:
        dx, dy = ev.angleDelta().x(), ev.angleDelta().y()
        span = self.t1 - self.t0
        mods = ev.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier) or \
                (abs(dy) > abs(dx) and not (mods & Qt.KeyboardModifier.ShiftModifier)):
            anchor = self.t_of(ev.position().x())
            factor = 0.8 if dy > 0 else 1.25
            new_span = min(self.duration, max(2.0, span * factor))
            frac = (anchor - self.t0) / span
            self.set_view(anchor - frac * new_span, anchor - frac * new_span + new_span)
        else:
            d = -(dx if dx else dy) / 120.0 * span * 0.1
            self.set_view(self.t0 + d, self.t1 + d)
        ev.accept()


class Timeline(QWidget):
    """Canvas + scrollbar + zoomknoppen."""
    seekRequested = Signal(float)
    rangeEdited = Signal(float, float)
    rangeFinished = Signal()
    clipClicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = _Canvas()
        self.bar = QScrollBar(Qt.Orientation.Horizontal)
        self.bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        lay.addWidget(self.canvas)
        row = QHBoxLayout(); row.setSpacing(4)
        self.btn_in = QPushButton("＋"); self.btn_out = QPushButton("－"); self.btn_all = QPushButton("Alles")
        self.btn_sel = QPushButton("Naar clip")
        for b, tip in ((self.btn_in, "Inzoomen"), (self.btn_out, "Uitzoomen"), (self.btn_all, "Hele set tonen"),
                       (self.btn_sel, "Zoom naar de geselecteerde clip")):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus); b.setToolTip(tip); b.setFixedHeight(22)
        self.btn_in.setFixedWidth(30); self.btn_out.setFixedWidth(30)
        row.addWidget(self.btn_in); row.addWidget(self.btn_out); row.addWidget(self.btn_all)
        row.addWidget(self.btn_sel); row.addWidget(self.bar, 1)
        lay.addLayout(row)
        c = self.canvas
        c.seekRequested.connect(self.seekRequested); c.rangeEdited.connect(self.rangeEdited)
        c.rangeFinished.connect(self.rangeFinished); c.clipClicked.connect(self.clipClicked)
        c.viewChanged.connect(self._sync_bar)
        self.bar.valueChanged.connect(self._bar_moved)
        self.btn_in.clicked.connect(lambda: self._zoom(0.5))
        self.btn_out.clicked.connect(lambda: self._zoom(2.0))
        self.btn_all.clicked.connect(lambda: c.set_view(0, c.duration))
        self.btn_sel.clicked.connect(self.zoom_to_selection)
        self._syncing = False

    def _zoom(self, factor: float) -> None:
        c = self.canvas
        mid = (c.t0 + c.t1) / 2 if not (c.t0 <= c.playhead <= c.t1) else c.playhead
        span = min(c.duration, max(2.0, (c.t1 - c.t0) * factor))
        c.set_view(mid - span / 2, mid + span / 2)

    def zoom_to_selection(self) -> None:
        c = self.canvas
        if c.sel:
            pad = max(5.0, (c.sel[1] - c.sel[0]) * 0.5)
            c.set_view(c.sel[0] - pad, c.sel[1] + pad)

    def _sync_bar(self, t0: float, t1: float) -> None:
        self._syncing = True
        c = self.canvas
        span = t1 - t0
        self.bar.setRange(0, int(max(0, c.duration - span) * 10))
        self.bar.setPageStep(int(span * 10))
        self.bar.setValue(int(t0 * 10))
        self._syncing = False

    def _bar_moved(self, v: int) -> None:
        if self._syncing:
            return
        c = self.canvas
        span = c.t1 - c.t0
        c.set_view(v / 10.0, v / 10.0 + span)

    # API
    def set_media(self, duration, peaks): self.canvas.set_media(duration, peaks)
    def set_peaks(self, peaks): self.canvas.set_peaks(peaks)
    def set_playhead(self, t: float) -> None:
        self.canvas.playhead = t
        self.canvas.update()
    def set_selection(self, rng): self.canvas.sel = rng; self.canvas.update()
    def set_others(self, others): self.canvas.others = others; self.canvas.update()
    def ensure_visible(self, t): self.canvas.ensure_visible(t)
