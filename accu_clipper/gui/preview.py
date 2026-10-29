"""Ingebouwde videospeler met live 4:5-voorbeeld (1080×1350): crop, zoom en branding.

De positie en schaal komen uit layout.compute_layout(), dezelfde functie die de export gebruikt.
Beeldinstellingen als helderheid/contrast/fades zijn niet in dit live voorbeeld zichtbaar; gebruik
'Toon exact exportframe' voor een controle door FFmpeg zelf."""
from __future__ import annotations
import os

from PySide6.QtCore import Qt, QSizeF, QRectF, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsView)

from ..layout import compute_layout
from ..model import OUT_W, OUT_H


class PreviewView(QGraphicsView):
    cropDragged = Signal(float, float)      # fx, fy
    logoMoved = Signal(float, float)        # cx, cy
    textMoved = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 34)))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scene_ = QGraphicsScene(self)
        self.scene_.setSceneRect(QRectF(-30, -30, OUT_W + 60, OUT_H + 60))
        self.setScene(self.scene_)
        self.frame = QGraphicsRectItem(0, 0, OUT_W, OUT_H)
        self.frame.setBrush(QBrush(Qt.GlobalColor.black))
        self.frame.setPen(QPen(QColor(90, 90, 100), 2))
        self.frame.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        self.scene_.addItem(self.frame)
        self.video = QGraphicsVideoItem(self.frame)
        self.video.setAspectRatioMode(Qt.AspectRatioMode.IgnoreAspectRatio)
        self.logo_item = QGraphicsPixmapItem(self.frame)
        self.logo_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.text_item = QGraphicsPixmapItem(self.frame)
        self.text_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.logo_item.hide(); self.text_item.hide()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.audio.setVolume(0.8)
        self._look = None
        self._st = None
        self._lay = None
        self._drag = None
        self._logo_cache: tuple | None = None

    # ------------------------------------------------------------------ bron
    def set_source(self, path: str | None) -> None:
        self.player.stop()
        if path and os.path.isfile(path):
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.pause()
        else:
            self.player.setSource(QUrl())

    # ------------------------------------------------------------------ opmaak
    def update_scene(self, info, look, st, text_png: str | None) -> None:
        """Herpositioneert video, logo en DJ-naam volgens de huidige instellingen."""
        self._look, self._st = look, st
        if info is not None and look is not None:
            lay = compute_layout(info.display_w, info.display_h, look)
            self._lay = lay
            self.video.setSize(QSizeF(lay.w, lay.h))
            self.video.setPos(lay.x, lay.y)
        if st is not None and st.logo_path and os.path.isfile(st.logo_path):
            width = max(8, int(round(st.logo_width * OUT_W)))
            key = (st.logo_path, width, os.path.getmtime(st.logo_path))
            if not self._logo_cache or self._logo_cache[0] != key:
                pm = QPixmap(st.logo_path)
                if not pm.isNull():
                    pm = pm.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
                self._logo_cache = (key, pm)
            pm = self._logo_cache[1]
            if not pm.isNull():
                self.logo_item.setPixmap(pm)
                self.logo_item.setOpacity(st.logo_opacity)
                self.logo_item.setPos(st.logo_cx * OUT_W - pm.width() / 2, st.logo_cy * OUT_H - pm.height() / 2)
                self.logo_item.show()
            else:
                self.logo_item.hide()
        else:
            self.logo_item.hide()
        if st is not None and text_png and os.path.isfile(text_png):
            pm = QPixmap(text_png)
            self.text_item.setPixmap(pm)
            self.text_item.setOpacity(st.dj_opacity)
            self.text_item.setPos(st.dj_cx * OUT_W - pm.width() / 2, st.dj_cy * OUT_H - pm.height() / 2)
            self.text_item.show()
        else:
            self.text_item.hide()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.fitInView(QRectF(-10, -10, OUT_W + 20, OUT_H + 20), Qt.AspectRatioMode.KeepAspectRatio)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.fitInView(QRectF(-10, -10, OUT_W + 20, OUT_H + 20), Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------------------ slepen
    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton or self._look is None:
            return super().mousePressEvent(ev)
        item = self.itemAt(ev.position().toPoint())
        sp = self.mapToScene(ev.position().toPoint())
        st, look = self._st, self._look
        if item is self.text_item and self.text_item.isVisible():
            self._drag = ("text", sp, (st.dj_cx, st.dj_cy))
        elif item is self.logo_item and self.logo_item.isVisible():
            self._drag = ("logo", sp, (st.logo_cx, st.logo_cy))
        elif self._lay is not None and self._lay.mode == "fill" and \
                (self._lay.overflow_x > 0 or self._lay.overflow_y > 0):
            self._drag = ("crop", sp, (look.fx, look.fy))
        else:
            self._drag = None
        if self._drag:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev) -> None:
        if not self._drag:
            return super().mouseMoveEvent(ev)
        kind, start, orig = self._drag
        sp = self.mapToScene(ev.position().toPoint())
        dx, dy = sp.x() - start.x(), sp.y() - start.y()
        if kind == "crop" and self._lay is not None:
            fx, fy = orig
            if self._lay.overflow_x > 0:
                fx = min(1.0, max(0.0, fx - dx / self._lay.overflow_x))
            if self._lay.overflow_y > 0:
                fy = min(1.0, max(0.0, fy - dy / self._lay.overflow_y))
            self.cropDragged.emit(fx, fy)
        else:
            cx = min(1.0, max(0.0, orig[0] + dx / OUT_W))
            cy = min(1.0, max(0.0, orig[1] + dy / OUT_H))
            (self.logoMoved if kind == "logo" else self.textMoved).emit(cx, cy)

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = None
        self.unsetCursor()
        super().mouseReleaseEvent(ev)
