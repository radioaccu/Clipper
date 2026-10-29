"""Geometrie van het 1080×1350-kader. Wordt door export én preview gebruikt,
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

    @property
    def overflow_x(self) -> int:
        return max(0, self.w - OUT_W)

    @property
    def overflow_y(self) -> int:
        return max(0, self.h - OUT_H)


def compute_layout(disp_w: float, disp_h: float, look: Look) -> Layout:
    """Berekent schaal en positie. Het beeld wordt nooit uitgerekt: één uniforme schaalfactor."""
    if disp_w <= 0 or disp_h <= 0:
        raise ValueError("Ongeldige beeldafmetingen")
    if look.crop_mode == "fit":
        s = min(OUT_W / disp_w, OUT_H / disp_h)
        w = min(OUT_W, _even_down(disp_w * s))
        h = min(OUT_H, _even_down(disp_h * s))
        x = (OUT_W - w) // 2
        y = (OUT_H - h) // 2
        return Layout("fit", w, h, x - (x % 2), y - (y % 2))
    zoom = min(max(look.zoom, 1.0), MAX_ZOOM)
    s = max(OUT_W / disp_w, OUT_H / disp_h) * zoom
    w = max(OUT_W, _even_up(disp_w * s))
    h = max(OUT_H, _even_up(disp_h * s))
    fx = min(max(look.fx, 0.0), 1.0)
    fy = min(max(look.fy, 0.0), 1.0)
    cx = int(round((w - OUT_W) * fx))
    cy = int(round((h - OUT_H) * fy))
    cx -= cx % 2
    cy -= cy % 2
    return Layout("fill", w, h, -cx, -cy)
