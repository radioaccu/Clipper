"""Rendert de DJ-naam naar een transparante PNG (dezelfde PNG wordt in preview én export gebruikt)."""
from __future__ import annotations
import os
import tempfile

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QBrush

from ..model import ExportSettings


def text_key(st: ExportSettings) -> tuple:
    return (st.dj_name.strip(), st.dj_size, st.dj_color, st.dj_outline, st.dj_bold)


def render_text_png(st: ExportSettings, out_dir: str | None = None) -> str | None:
    """Maakt een PNG op exportschaal (pixels = exportpixels). Geeft het pad terug of None bij lege naam."""
    text = st.dj_name.strip()
    if not text:
        return None
    font = QFont()
    font.setPixelSize(max(8, int(st.dj_size)))
    font.setBold(bool(st.dj_bold))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    fm = QFontMetricsF(font)
    pad = max(6, int(st.dj_size * 0.25))
    w = int(fm.horizontalAdvance(text)) + 2 * pad
    h = int(fm.height()) + 2 * pad
    w += w % 2
    h += h % 2
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    path = QPainterPath()
    path.addText(QPointF(pad, pad + fm.ascent()), font, text)
    if st.dj_outline:
        # donkere rand + zachte schaduw → leesbaar op elke achtergrond
        shadow = QPainterPath(path)
        shadow.translate(max(2.0, st.dj_size * 0.04), max(2.0, st.dj_size * 0.04))
        p.fillPath(shadow, QBrush(QColor(0, 0, 0, 120)))
        pen = QPen(QColor(0, 0, 0, 235))
        pen.setWidthF(max(2.0, st.dj_size * 0.12))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.fillPath(path, QBrush(QColor(st.dj_color)))
    p.end()
    out_dir = out_dir or tempfile.mkdtemp(prefix="accu_txt_")
    fn = os.path.join(out_dir, f"dj_{abs(hash(text_key(st))) % 10**8}.png")
    img.save(fn, "PNG")
    return fn
