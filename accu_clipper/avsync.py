"""Automatische beeld/geluid-synchronisatie (lip-sync) voor opgenomen sets.

Probleem: bij een opname met OBS, een capturekaart of een camera plus een losse mixer-opname loopt het beeld vaak
een vaste tijd achter (of voor) op het geluid. Soms groeit dat verschil langzaam (klok-drift).

Werkwijze (signaalanalyse, geen cloud):
  1. Op ±10 plekken verspreid over de set wordt 40 s beeld (klein, grijs) en geluid gedecodeerd.
  2. Beeld → 'visuele gebeurtenissen' per 10 ms: hoeveel het beeld verandert en hoeveel het oplicht
     (lampen/strobo die op de kick flitsen, bewegende DJ, publiek dat springt, camerawissels).
     Geluid → 'muzikale gebeurtenissen' per 10 ms: spectrale flux (vooral kicks/bas en nieuwe klanken).
  3. Per plek wordt de kruiscorrelatie berekend voor verschuivingen tot ±max_lag; alle plekken samen bepalen
     de beste verschuiving. Daarna wordt per plek fijn bijgesteld en kan een lineaire drift worden gevonden.

Bij muziek met een vaste beat herhaalt de correlatie zich elke tel (bij 128 BPM elke 0,47 s). Daarom geven we
naast de beste waarde ook de alternatieven en een betrouwbaarheid terug; de interface laat een kort testfragment
maken zodat je het resultaat met eigen ogen en oren controleert.

Teken-afspraak (overal in de app): offset > 0  =  het BEELD loopt ACHTER op het geluid (je hoort de kick
eerder dan je hem ziet). De correctie schuift het geluid dan later."""
from __future__ import annotations
import subprocess
import threading
from dataclasses import dataclass, field, asdict
from typing import Callable

import numpy as np

from .ffmpeg_tools import SourceInfo, ToolError
from .platform_paths import no_window_kwargs

RATE = 100                 # analyseraster: 100 waarden per seconde (10 ms)
SR = 24000                 # audio-samplerate voor de analyse (hop 240 = 10 ms)
HOP = SR // RATE
WIN = 512 
VW, VH = 64, 36            # beeld wordt verkleind tot 64×36 grijswaarden


class SyncCancelled(Exception):
    pass


@dataclass
class SyncResult:
    offset: float = 0.0                 # s, > 0 = beeld loopt achter (op t = 0)
    drift: float = 0.0                  # s per s (offset op tijd t = offset + drift·t)
    confidence: float = 0.0             # 0..1
    level: str = "laag"                 # "hoog" | "redelijk" | "laag"
    candidates: list = field(default_factory=list)   # [(offset_s, relatieve score 0..1)], beste eerst
    windows: list = field(default_factory=list)      # [(midden_s, offset_s, piekwaarde)]
    note: str = ""

    def offset_at(self, t: float) -> float:
        return self.offset + self.drift * t

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------- signalen
def _run_bytes(cmd: list[str], cancel: threading.Event | None) -> bytes:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs())
    chunks: list[bytes] = []
    try:
        while True:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise SyncCancelled()
            b = proc.stdout.read(1 << 20)  # type: ignore[union-attr]
            if not b:
                break
            chunks.append(b)
    finally:
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        except Exception:
            pass
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""  # type: ignore[union-attr]
    proc.wait()
    if proc.stderr:
        proc.stderr.close()
    if proc.returncode != 0 and not chunks:
        raise ToolError("Kon de bron niet lezen voor de sync-analyse.\n" + err[-300:])
    return b"".join(chunks)


def video_activity(ffmpeg: str, path: str, t0: float, dur: float,
                   cancel: threading.Event | None = None) -> np.ndarray:
    """Visuele gebeurtenissen op het 100 Hz-raster, beginnend op t0.
    Het fps-filter zet elk bronframe op zijn eigen tijdstip (dubbele frames geven een verschil van 0)."""
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", path,
           "-map", "0:v:0", "-an", "-vf", f"fps={RATE}:round=near,scale={VW}:{VH}:flags=area,format=gray",
           "-f", "rawvideo", "-"]
    raw = _run_bytes(cmd, cancel)
    n = len(raw) // (VW * VH)
    if n < RATE * 3:
        return np.zeros(0, dtype=np.float32)
    g = np.frombuffer(raw[: n * VW * VH], dtype=np.uint8).reshape(n, VH, VW).astype(np.float32)
    d = np.diff(g, axis=0, prepend=g[:1])
    absd = np.abs(d).mean(axis=(1, 2))                       # beweging en lichtwissel
    pos = np.maximum(d, 0).mean(axis=(1, 2))                 # oplichten (flitsen op de beat)
    # regio's: een flitsende lamp in een hoek telt zo even zwaar als een grote beweging
    reg = np.abs(d).reshape(n, 3, VH // 3, 4, VW // 4).mean(axis=(2, 4)).reshape(n, 12)
    regmax = reg.max(axis=1)
    return _combine([absd, pos, regmax])


def audio_activity(ffmpeg: str, path: str, t0: float, dur: float,
                   cancel: threading.Event | None = None) -> np.ndarray:
    """Muzikale gebeurtenissen (spectrale flux, extra gewicht op de bas) op het 100 Hz-raster vanaf t0."""
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", path,
           "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    raw = _run_bytes(cmd, cancel)
    x = np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)
    if len(x) < SR * 3:
        return np.zeros(0, dtype=np.float32)
    # frames gecentreerd op k·HOP, zodat waarde k bij tijd k/RATE hoort (zelfde raster als het beeld)
    x = np.concatenate([np.zeros(WIN // 2, np.float32), x, np.zeros(WIN // 2, np.float32)])
    n = (len(x) - WIN) // HOP + 1
    seg = np.lib.stride_tricks.sliding_window_view(x, WIN)[::HOP][:n]
    mag = np.abs(np.fft.rfft(seg * np.hanning(WIN).astype(np.float32), axis=1))
    freqs = np.fft.rfftfreq(WIN, 1.0 / SR)
    lm = np.log1p(mag * 20.0)
    flux = np.maximum(np.diff(lm, axis=0, prepend=lm[:1]), 0)
    full = flux[:, (freqs >= 30) & (freqs < 8000)].mean(axis=1)
    bass = flux[:, (freqs >= 30) & (freqs < 200)].mean(axis=1)
    return _combine([full, bass])


def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return x
    k = np.ones(n, dtype=np.float64) / n
    return np.convolve(x, k, mode="same")


def _prep(x: np.ndarray) -> np.ndarray:
    """Log-compressie, trend eruit (1 s), normaliseren, uitschieters (bv. scènewissel) begrenzen."""
    x = np.log1p(np.asarray(x, dtype=np.float64) * 10.0)
    x = x - _smooth(x, RATE)
    s = x.std()
    if s < 1e-9:
        return np.zeros_like(x)
    return np.clip(x / s, -4, 6)


def _combine(parts: list[np.ndarray]) -> np.ndarray:
    out = np.zeros_like(parts[0], dtype=np.float64)
    for p in parts:
        out += _prep(p)
    out = _smooth(out, 3)                    # 30 ms: frame-impulsen (25/30 fps) uitsmeren
    s = out.std()
    return (out - out.mean()) / s if s > 1e-9 else out


def _parabolic(c: np.ndarray, i: int) -> float:
    if 0 < i < len(c) - 1:
        y0, y1, y2 = c[i - 1], c[i], c[i + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            return i + 0.5 * (y0 - y2) / den
    return float(i)


def _peaks(c: np.ndarray, min_sep: int) -> list[int]:
    idx = [i for i in range(1, len(c) - 1) if c[i] >= c[i - 1] and c[i] >= c[i + 1]]
    idx.sort(key=lambda i: -c[i])
    out: list[int] = []
    for i in idx:
        if all(abs(i - j) >= min_sep for j in out):
            out.append(i)
    return out


# ----------------------------------------------------------------------------- detectie
def window_starts(duration: float, n: int, length: float) -> list[float]:
    if duration <= length + 1:
        return [0.0]
    lo, hi = min(duration * 0.03, 60.0), max(0.0, duration - length - min(duration * 0.03, 30.0))
    if hi <= lo:
        return [max(0.0, (duration - length) / 2)]
    n = max(1, min(n, int((hi - lo) / length) + 1))
    return [float(t) for t in np.linspace(lo, hi, n)]


def beat_period(a: np.ndarray) -> float | None:
    """Tel-lengte in samples (100 Hz) uit de autocorrelatie van het geluid; None als er geen duidelijke beat is."""
    x = a - a.mean()
    n = len(x)
    size = 1 << int(np.ceil(np.log2(2 * n)))
    X = np.fft.rfft(x, size)
    ac = np.fft.irfft(np.abs(X) ** 2, size)[: int(1.3 * RATE)]
    ac = ac / (ac[0] + 1e-12)
    lo, hi = int(0.28 * RATE), int(1.2 * RATE)                      # 50 – 214 BPM
    i = lo + int(np.argmax(ac[lo:hi]))
    if ac[i] < 0.15:
        return None
    return _parabolic(ac, i)


def _shift(c: np.ndarray, k: float) -> np.ndarray:
    """c(L + k) via lineaire interpolatie (buiten bereik: 0)."""
    idx = np.arange(len(c)) + k
    return np.interp(idx, np.arange(len(c)), c, left=0.0, right=0.0)


def comb(c: np.ndarray, period: float | None) -> np.ndarray:
    """Haalt het deel van de correlatie weg dat zich elke tel herhaalt (de beat): (1 − ½T⁻ᴾ − ½Tᴾ)²-filter.
    Een zuiver periodieke kam valt weg; een eenmalige samenhang (fill, drop, strobo) blijft over."""
    if not period:
        return c
    return (1.5 * c - _shift(c, -period) - _shift(c, period)
            + 0.25 * (_shift(c, -2 * period) + _shift(c, 2 * period)))


def detect(ffmpeg: str, info: SourceInfo, max_lag: float = 2.0, n_windows: int = 10, window: float = 40.0,
           progress: Callable[[float, str], None] | None = None,
           cancel: threading.Event | None = None) -> SyncResult:
    if info.audio_only or not info.has_audio:
        raise ToolError("Sync-detectie heeft een video met beeld én geluid nodig.")
    L = int(round(max_lag * RATE))
    E = L + int(2.5 * RATE)                      # extra bereik voor het beat-filter (2 tellen van max. 1,2 s)
    margin = E / RATE + 0.5
    starts = window_starts(info.duration, n_windows, window)
    curves: list[tuple[float, np.ndarray, np.ndarray]] = []
    for k, t0 in enumerate(starts):
        if cancel is not None and cancel.is_set():
            raise SyncCancelled()
        if progress:
            progress(k / len(starts), f"Beeld en geluid vergelijken: plek {k + 1} van {len(starts)}…")
        # geluid met marge aan beide kanten zodat elke verschuiving volledig overlapt
        a_t0 = max(0.0, t0 - margin)
        a = audio_activity(ffmpeg, info.path, a_t0, window + 2 * margin, cancel)
        v = video_activity(ffmpeg, info.path, t0, window, cancel)
        if len(a) < RATE * 5 or len(v) < RATE * 5:
            continue
        pad = int(round((t0 - a_t0) * RATE))          # beeld-sample 0 hoort bij audio-sample 'pad'
        nv = len(v)
        c = np.zeros(2 * E + 1)
        for j, lag in enumerate(range(-E, E + 1)):
            # beeld op t toont wat 'lag' eerder klonk: v[t] ↔ a[t − lag]
            s = pad - lag
            lo, hi = max(0, -s), min(nv, len(a) - s)
            if hi - lo >= RATE * 5:
                c[j] = float(np.dot(v[lo:hi], a[s + lo:s + hi]) / (hi - lo))
        period = beat_period(a[pad:pad + nv])
        cf = comb(c, period)[E - L:E + L + 1]
        curves.append((t0 + window / 2, c[E - L:E + L + 1], cf))
    if progress:
        progress(0.97, "Resultaat berekenen…")
    if not curves:
        raise ToolError("Te weinig beeld of geluid om de sync te meten (is de video erg kort?).")
    return _decide(curves, L, info.duration)


def _decide(curves: list[tuple[float, np.ndarray, np.ndarray]], L: int, duration: float) -> SyncResult:
    # Keuze op de beat-gefilterde curves; fijnafstelling per plek op de ruwe (scherpere) curve.
    # Bij klok-drift verschuift de piek per plek; daarom ook proberen met een lineaire drift (max ±3 s per uur).
    mids = np.array([m for m, _, _ in curves])
    t_ref = float(mids.mean())

    def summed(b: float) -> np.ndarray:
        return np.sum([_shift(cf, b * (m - t_ref) * RATE) for m, _, cf in curves], axis=0) / len(curves)

    total = summed(0.0)
    best_b = 0.0
    span = float(mids.max() - mids.min()) if len(curves) > 1 else 0.0
    if len(curves) >= 4 and span > 300:
        step = 0.015 / span                       # 15 ms over de hele meetspanne
        top0 = float(total.max())
        for b in np.arange(-3.0 / 3600, 3.0 / 3600 + step / 2, step):
            tb = summed(float(b))
            if float(tb.max()) > max(top0 * 1.08, float(total.max())):   # drift moet duidelijk beter passen
                total, best_b = tb, float(b)
    # lichte voorkeur voor kleine vertragingen (in de praktijk meestal < 1 s) bij twijfel tussen beat-pieken
    prior = 1.0 - 0.06 * np.abs(np.arange(-L, L + 1)) / RATE
    total = np.where(total > 0, total * prior, total)
    peaks = _peaks(total, min_sep=int(0.12 * RATE))
    if not peaks:
        return SyncResult(note="Geen duidelijke samenhang tussen beeld en geluid gevonden.")
    best = peaks[0]
    best_off = (_parabolic(total, best) - L) / RATE - best_b * t_ref
    top = total[best]
    noise = float(np.median(np.abs(total - np.median(total)))) + 1e-9
    second = total[peaks[1]] if len(peaks) > 1 else float(np.median(total))
    cands = []
    for p in peaks[:4]:
        rel = float((total[p] - np.median(total)) / (top - np.median(total) + 1e-9))
        if rel > 0.35 or p == best:
            cands.append((round(float((_parabolic(total, p) - L) / RATE), 3), round(max(0.0, min(1.0, rel)), 2)))
    # per plek: fijn bijstellen rond de gezamenlijke beste waarde (±0,15 s, minder dan een halve tel)
    win_res = []
    agree = 0
    for mid, c, cf in curves:
        exp = best + int(round(best_b * (mid - t_ref) * RATE))          # verwachte piek op deze plek
        lo, hi = max(0, exp - 15), min(len(c), exp + 16)
        i = lo + int(np.argmax(c[lo:hi]))
        off = (_parabolic(c, i) - L) / RATE
        own = int(np.argmax(cf))
        if abs(own - exp) <= 4:
            agree += 1
        win_res.append((round(float(mid), 1), round(float(off), 3), round(float(c[i]), 4)))
    sep = (top - second) / (top - float(np.median(total)) + 1e-9)       # 0 = twee even sterke pieken
    snr = (top - float(np.median(total))) / noise
    agree_frac = agree / len(curves)
    conf = float(np.clip(0.45 * min(1.0, sep / 0.35) + 0.35 * agree_frac + 0.2 * min(1.0, snr / 8.0), 0, 1))
    if len(curves) == 1:
        conf *= 0.8
    level = "hoog" if conf >= 0.7 else ("redelijk" if conf >= 0.45 else "laag")

    # drift: lineaire fit door de fijn bijgestelde waarden (gewogen), alleen als die duidelijk en consistent is
    offset, drift = best_off, 0.0
    note = ""
    good = [(m, o, w) for m, o, w in win_res if w > 0]
    if len(good) >= 4:
        m = np.array([g[0] for g in good]); o = np.array([g[1] for g in good]); w = np.array([g[2] for g in good])
        A = np.vstack([np.ones_like(m), m]).T * np.sqrt(w)[:, None]
        coef, *_ = np.linalg.lstsq(A, o * np.sqrt(w), rcond=None)
        resid = o - (coef[0] + coef[1] * m)
        flat_resid = o - np.average(o, weights=w)
        span = abs(coef[1]) * (m.max() - m.min())
        if span > 0.04 and np.sqrt(np.average(resid ** 2, weights=w)) < 0.025 \
                and np.sqrt(np.average(flat_resid ** 2, weights=w)) > 0.02:
            offset, drift = float(coef[0]), float(coef[1])
            note = (f"Het verschil loopt op tijdens de set ({drift * 3600 * 1000:+.0f} ms per uur): "
                    "dat wordt ook gecorrigeerd.")
        else:
            offset = float(np.average(o, weights=w))
    offset, drift = float(offset), float(drift)
    cands = [(round(offset, 3), cands[0][1])] + [c for c in cands[1:] if abs(c[0] - offset) > 0.06]
    if level != "hoog" and len(cands) > 1:
        note = (note + " " if note else "") + ("Er zijn meerdere mogelijke waarden (de beat herhaalt zich). "
                                               "Maak een testfragment en kies zo nodig een alternatief.")
    return SyncResult(offset=round(offset, 3), drift=drift, confidence=round(conf, 2), level=level,
                      candidates=cands, windows=win_res, note=note.strip())


def describe_offset(offset: float) -> str:
    ms = round(offset * 1000)
    if abs(ms) < 5:
        return "beeld en geluid lopen gelijk"
    if ms > 0:
        return f"het beeld loopt {ms} ms achter op het geluid"
    return f"het beeld loopt {-ms} ms vóór op het geluid"
