"""Heuristische highlightdetectie voor DJ-sets.

LET OP: dit is signaalanalyse (energie, bas, spectrale verandering), geen AI en geen 'begrip' van muziek.
De scores zijn relatief ten opzichte van de rest van dezelfde set."""
from __future__ import annotations
from dataclasses import dataclass

import numpy as np

from .features import Features, FPS
from .timecode import format_time

MODES = {
    "balanced": "Balanced – mix van energie, drops en overgangen, gespreid over de set",
    "drops": "Drops – bas en energie stijgen sterk na een rustiger stuk",
    "transitions": "Transitions – duidelijke klankverandering (nieuwe track, breakdown, filter)",
    "high_energy": "High energy – aanhoudend zware, energieke stukken",
}
GRID = 0.5          # seconden per analysestap
WEAK_BELOW = 0.35   # scores onder deze waarde worden als 'zwak' gemarkeerd


@dataclass
class Suggestion:
    start: float
    duration: float
    score: float
    reason: str
    kind: str
    weak: bool
    event_offset: float = 0.0   # waar in de clip het opvallende moment zit (s)


def _downsample(x: np.ndarray, step: int) -> np.ndarray:
    n = (len(x) // step) * step
    return x[:n].reshape(-1, step).mean(axis=1)


def _norm(x: np.ndarray, lo_p: float = 10, hi_p: float = 95) -> np.ndarray:
    lo, hi = np.percentile(x, lo_p), np.percentile(x, hi_p)
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0, 1)


def _win_mean(cs: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Gemiddelde over [a, b) (indices, geklemd) uit een cumulatieve som."""
    n = len(cs) - 1
    a = np.clip(a, 0, n)
    b = np.clip(b, 0, n)
    cnt = np.maximum(b - a, 1)
    return (cs[b] - cs[a]) / cnt


def suggest(feats: Features, clip_duration: float, count: int, mode: str = "balanced",
            avoid: list[tuple[float, float]] | None = None) -> tuple[list[Suggestion], str]:
    """Geeft (suggesties op tijd gesorteerd, eerlijke samenvattende melding)."""
    if mode not in MODES:
        raise ValueError(f"Onbekende modus: {mode}")
    step = int(round(GRID * FPS))
    cols = {k: _downsample(feats.col(k), step) for k in ("rms_db", "bass_db", "lowmid_db", "mid_db", "high_db", "flux")}
    n = len(cols["rms_db"])
    total = feats.duration
    if total < clip_duration + 1 or n < 10:
        return [], f"De opname ({format_time(total)}) is korter dan de gekozen clipduur."
    E, B, L, M, H, F = (_norm(cols[k]) for k in ("rms_db", "bass_db", "lowmid_db", "mid_db", "high_db", "flux"))
    # zeer stille stukken (<-55 dB rms) tellen nooit mee
    silent = cols["rms_db"] < -55
    energy = 0.55 * E + 0.45 * B
    energy[silent] = 0

    def cs(x):
        return np.concatenate([[0.0], np.cumsum(x)])
    c_energy, c_B, c_E, c_F = cs(energy), cs(B), cs(E), cs(F)
    c_bands = [cs(x) for x in (B, L, M, H, E)]
    idx = np.arange(n)
    pre_n, post_n = int(8 / GRID), int(8 / GRID)
    pre_b = _win_mean(c_B, idx - pre_n, idx)
    post_b = _win_mean(c_B, idx, idx + post_n)
    pre_e = _win_mean(c_E, idx - pre_n, idx)
    post_e = _win_mean(c_E, idx, idx + post_n)
    drop_raw = np.clip(0.6 * (post_b - pre_b) + 0.4 * (post_e - pre_e), 0, None)
    drop = _norm(drop_raw * (0.4 + 0.6 * post_b), 50, 98)
    drop[:pre_n] = 0

    tw = int(6 / GRID)
    dist = np.zeros(n)
    for c in c_bands:
        dist += np.abs(_win_mean(c, idx, idx + tw) - _win_mean(c, idx - tw, idx))
    flux_local = _win_mean(c_F, idx - int(2 / GRID), idx + int(2 / GRID))
    trans = _norm(0.7 * _norm(dist, 50, 98) + 0.3 * flux_local, 50, 98)
    trans[:tw] = 0

    dur_n = max(2, int(round(clip_duration / GRID)))
    leads = {"balanced": 0.30, "drops": 0.30, "transitions": 0.40, "high_energy": 0.0}
    lead = leads[mode]
    start_idx = np.clip(np.round(idx - lead * dur_n).astype(int), 0, max(0, n - dur_n))
    win_energy = _win_mean(c_energy, start_idx, start_idx + dur_n)
    if mode == "drops":
        score = 0.7 * drop + 0.3 * win_energy
    elif mode == "transitions":
        score = 0.7 * trans + 0.3 * win_energy
    elif mode == "high_energy":
        score = 0.9 * win_energy + 0.1 * drop
    else:
        score = 0.35 * win_energy + 0.35 * drop + 0.30 * trans

    max_start = total - clip_duration
    starts = np.minimum(start_idx * GRID, max_start)
    order = np.argsort(-score)
    avoid = avoid or []

    def ok(s: float, chosen: list[float], gap: float) -> bool:
        for (a, b) in avoid:
            if s < b and s + clip_duration > a:
                return False
        return all(abs(s - c) >= gap for c in chosen)

    chosen_idx: list[int] = []
    chosen_starts: list[float] = []
    spread = max(clip_duration * 1.25, total / (count * 2.5)) if mode == "balanced" else clip_duration * 1.25
    for gap in (spread, clip_duration * 1.05):
        for i in order:
            if len(chosen_idx) >= count:
                break
            if score[i] <= 0.02:
                break
            s = float(starts[i])
            if i in chosen_idx or not ok(s, chosen_starts, gap):
                continue
            chosen_idx.append(int(i))
            chosen_starts.append(s)
        if len(chosen_idx) >= count:
            break

    sugg: list[Suggestion] = []
    raw_bass_db = cols["bass_db"]
    for i, s in zip(chosen_idx, chosen_starts):
        sc = float(score[i])
        comps = {"drop": float(drop[i]), "trans": float(trans[i]), "energy": float(win_energy[i])}
        if mode == "high_energy":
            top = "energy"
        elif mode == "drops":
            top = "drop"
        elif mode == "transitions":
            top = "trans"
        else:
            top = max(comps, key=comps.get)
        t_event = i * GRID
        off = max(0.0, min(clip_duration, t_event - s))
        if top == "drop":
            a = max(0, i - pre_n)
            db = float(raw_bass_db[i:i + post_n].mean() - raw_bass_db[a:i].mean()) if i > a else 0.0
            if db < 3.0:
                reason = (f"Geen duidelijke drop gevonden; fragment gekozen op energie "
                          f"(bas {db:+.1f} dB t.o.v. de 8 s ervoor)")
                top = "energy"
            else:
                shown = f"{db:+.1f}" if db < 30 else "+30 of meer"
                reason = f"Mogelijke drop op +{off:.0f} s: bas {shown} dB t.o.v. de 8 s ervoor"
        elif top == "trans":
            reason = f"Duidelijke klankverandering op +{off:.0f} s (overgang/breakdown)"
        else:
            pct = int(round(100 * float(np.mean(win_energy <= win_energy[i]))))
            reason = f"Aanhoudend energiek fragment (energierijker dan {pct}% van alle fragmenten in deze set)"
        weak = sc < WEAK_BELOW
        if weak:
            reason += " – ZWAK: weinig opvallend ten opzichte van de rest"
        sugg.append(Suggestion(round(s * 2) / 2, clip_duration, sc, reason, top, weak, off))
    sugg.sort(key=lambda g: g.start)

    n_weak = sum(1 for g in sugg if g.weak)
    note = f"{len(sugg)} suggesties gevonden (gevraagd: {count})."
    if len(sugg) < count:
        note += (f" Er zijn niet meer niet-overlappende momenten gevonden die opvallen in deze modus; "
                 f"probeer een andere modus of een kortere duur.")
    if n_weak:
        note += f" {n_weak} daarvan zijn zwak (minder geschikt) en zijn zo gemarkeerd."
    note += " Heuristische signaalanalyse, geen AI: controleer elke suggestie met het gehoor."
    return sugg, note
