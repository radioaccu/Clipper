"""Tracklists en hoofdstukken omzetten naar clips.

Herkent regels zoals:
    00:00 Intro
    1. [12:34] Artiest – Titel
    01:02:03 - Artiest - Titel
    Artiest - Titel (1:02:03)
    05:00 - 09:30 Artiest - Titel      (bereik: start = eerste tijd)
Regels zonder tijdstempel worden overgeslagen."""
from __future__ import annotations
import re
from dataclasses import dataclass

_TS = re.compile(r"(?<![\d:])(\d{1,2}:)?(\d{1,2}):(\d{2})(?![\d:])")
_SEP = re.compile(r"^\s*[\-–—|:•·]+\s*|\s*[\-–—|:•·]+\s*$")
_BRACKETS = re.compile(r"[\[\(]\s*[\]\)]")


@dataclass
class Track:
    start: float
    title: str
    end: float | None = None   # begin van de volgende track (of einde van de set)


def _to_seconds(m: re.Match) -> float:
    h = int(m.group(1)[:-1]) if m.group(1) else 0
    mi, s = int(m.group(2)), int(m.group(3))
    if s >= 60 or (m.group(1) and mi >= 60):
        raise ValueError
    return h * 3600 + mi * 60 + s


def parse_tracklist(text: str, total: float | None = None) -> list[Track]:
    tracks: list[Track] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        matches = list(_TS.finditer(line))
        if not matches:
            continue
        try:
            start = _to_seconds(matches[0])
        except ValueError:
            continue
        title = line
        for m in reversed(matches[:2]):            # tijdstempel(s) uit de titel halen
            title = title[:m.start()] + " " + title[m.end():]
        title = _BRACKETS.sub(" ", title)
        title = re.sub(r"^\s*\d{1,3}[.)]\s+", "", title)       # nummering "1." of "01)"
        title = _SEP.sub("", title.strip())
        title = re.sub(r"\s{2,}", " ", title).strip(" -–—|")
        tracks.append(Track(float(start), title or f"Track op {matches[0].group(0)}"))
    # sorteren, dubbele tijden en tijden voorbij het einde weglaten
    tracks.sort(key=lambda t: t.start)
    out: list[Track] = []
    for t in tracks:
        if total is not None and t.start >= total - 1:
            continue
        if out and abs(out[-1].start - t.start) < 1:
            continue
        out.append(t)
    for a, b in zip(out, out[1:]):
        a.end = b.start
    if out:
        out[-1].end = total
    return out


def tracks_from_chapters(chapters: list[dict], total: float | None = None) -> list[Track]:
    out = []
    for ch in chapters or []:
        try:
            s = float(ch["start"])
        except (KeyError, TypeError, ValueError):
            continue
        e = ch.get("end")
        out.append(Track(s, (ch.get("title") or "").strip() or "Hoofdstuk",
                         float(e) if e is not None else None))
    out.sort(key=lambda t: t.start)
    if total is not None:
        out = [t for t in out if t.start < total - 1]
    return out


def tracks_to_ranges(tracks: list[Track], duration: float, offset: float, total: float,
                     stop_at_next: bool = True) -> list[tuple[float, float, str]]:
    """Maakt (start, einde, titel) per track: start = trackbegin + offset, vaste duur.
    Met stop_at_next eindigt een clip uiterlijk waar de volgende track begint."""
    res = []
    for t in tracks:
        s = max(0.0, t.start + offset)
        limit = total
        if stop_at_next and t.end is not None:
            limit = min(limit, t.end)
        e = min(s + duration, limit)
        if e - s < 2.0:           # te kort (offset voorbij de track, of track korter dan 2 s)
            continue
        res.append((s, e, t.title))
    return res
