"""Tijdnotatie: seconden, MM:SS en HH:MM:SS (ook met decimalen)."""
from __future__ import annotations
import re

_NUM = re.compile(r"^\d+(?:[.,]\d+)?$")


def parse_time(text: str) -> float:
    """Zet '90', '90,5', '1:30', '01:02:03.25' om naar seconden.

    Gooit ValueError bij ongeldige invoer."""
    if text is None:
        raise ValueError("Lege tijd")
    s = str(text).strip().replace(" ", "")
    if not s:
        raise ValueError("Lege tijd")
    parts = s.split(":")
    if len(parts) > 3:
        raise ValueError(f"Ongeldige tijd: {text!r}")
    total = 0.0
    for i, p in enumerate(parts):
        if not _NUM.match(p):
            raise ValueError(f"Ongeldige tijd: {text!r}")
        v = float(p.replace(",", "."))
        if i > 0 and len(parts) > 1 and v >= 60 and i != 0:
            # minuten/seconden binnen een groter veld moeten < 60 zijn
            raise ValueError(f"Minuten/seconden moeten onder 60 zijn: {text!r}")
        total = total * 60 + v
    return total


def format_time(seconds: float, decimals: int = 0, force_hours: bool = False) -> str:
    """Seconden → 'MM:SS' of 'HH:MM:SS' (optioneel met decimalen)."""
    if seconds < 0:
        seconds = 0.0
    q = 10 ** decimals
    total = int(round(seconds * q))
    whole, frac = divmod(total, q)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    base = f"{h:02d}:{m:02d}:{s:02d}" if (h or force_hours) else f"{m:02d}:{s:02d}"
    if decimals:
        base += f".{frac:0{decimals}d}"
    return base
