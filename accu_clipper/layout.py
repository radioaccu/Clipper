"""Geometrie van het exportkader (bijv. 1080×1350 of 1920×1080). Wordt door export én preview gebruikt,
zodat wat je ziet overeenkomt met wat er geëxporteerd wordt."""
from __future__ import annotations
import math
from dataclasses import dataclass

from .model import Look, OUT_W, OUT_H

MAX_ZOOM = 4.0


def _even_up(v: float) -> int:
    n = int(math.ceil(v - 1e-9))
    return n + (n % 2)


def _even_down(v: float) -> int:
    n = int(math.floor(v + 1e-9))
    return n - (n % 2)


@dataclass(frozen=True)
class Layout:
    mode: str      # "fill" of "fit"
    w: int         # afmeting van het geschaalde beeld in exportpixels
    h: int
    x: int         # linkerbovenhoek van het geschaalde beeld t.o.v. het kader (fill: ≤ 0, fit: ≥ 0)
    y: int
    out_w: int = OUT_W   # afmeting van het kader zelf
    out_h: int = OUT_H

    @property
    def overflow_x(self) -> int:
        return max(0, self.w - self.out_w)

    @property
    def overflow_y(self) -> int:
        return max(0, self.h - self.out_h)


def compute_layout(disp_w: float, disp_h: float, look: Look, out_w: int = OUT_W, out_h: int = OUT_H) -> Layout:
    """Berekent schaal en positie in een kader van out_w × out_h.
    Het beeld wordt nooit uitgerekt: één uniforme schaalfactor."""
    if disp_w <= 0 or disp_h <= 0:
        raise ValueError("Ongeldige beeldafmetingen")
    if look.crop_mode == "fit":
        s = min(out_w / disp_w, out_h / disp_h)
        w = min(out_w, _even_down(disp_w * s))
        h = min(out_h, _even_down(disp_h * s))
        x = (out_w - w) // 2
        y = (out_h - h) // 2
        return Layout("fit", w, h, x - (x % 2), y - (y % 2), out_w, out_h)
    zoom = min(max(look.zoom, 1.0), MAX_ZOOM)
    s = max(out_w / disp_w, out_h / disp_h) * zoom
    w = max(out_w, _even_up(disp_w * s))
    h = max(out_h, _even_up(disp_h * s))
    fx = min(max(look.fx, 0.0), 1.0)
    fy = min(max(look.fy, 0.0), 1.0)
    cx = int(round((w - out_w) * fx))
    cy = int(round((h - out_h) * fy))
    cx -= cx % 2
    cy -= cy % 2
    return Layout("fill", w, h, -cx, -cy, out_w, out_h)
