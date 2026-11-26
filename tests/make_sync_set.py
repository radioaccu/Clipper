"""Maakt een nagebootste DJ-set-opname waarin het beeld een bekende tijd achterloopt op het geluid.

    python tests/make_sync_set.py uit.mp4 --lag 0.36 [--drift-ms-per-hour 0] [--dur 180] [--bpm 126]

Geluid: kicks op de beat, hi-hats, een breakdown zonder kick en onregelmatige 'fills' (ruisuitbarstingen).
Beeld: lampen die (meestal) op de kick flitsen, een DJ-blok dat op de beat beweegt, strobo bij de fills,
camerarustis. Elk beeldgebeuren verschijnt 'lag' seconden NA het bijbehorende geluid (beeld loopt achter)."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

FF = os.environ.get("ACCU_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--lag", type=float, default=0.36)
    ap.add_argument("--drift-ms-per-hour", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=180.0)
    ap.add_argument("--bpm", type=float, default=126.0)
    ap.add_argument("--fps", type=float, default=25.0)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    sr, dur, beat = 48000, a.dur, 60.0 / a.bpm
    n = int(sr * dur)
    y = np.zeros(n, np.float32)
    t_k = np.arange(int(0.06 * sr)) / sr
    kick = (np.sin(2 * np.pi * (50 + 90 * np.exp(-t_k * 40)) * t_k) * np.exp(-t_k * 25)).astype(np.float32)
    t_h = np.arange(int(0.03 * sr)) / sr
    hat = (rng.standard_normal(len(t_h)) * np.exp(-t_h * 150) * 0.15).astype(np.float32)
    events = []                              # (tijd, sterkte, soort)
    bd_lo, bd_hi = dur * 0.45, dur * 0.45 + 16 * beat            # breakdown
    t = 0.5
    while t < dur - 0.2:
        if not (bd_lo <= t < bd_hi):
            i = int(t * sr)
            y[i:i + len(kick)] += kick[: max(0, min(len(kick), n - i))] * 0.8
            if rng.random() < 0.75:
                events.append((t, 0.6 + 0.4 * rng.random(), "kick"))
        th = t + beat / 2
        i = int(th * sr)
        if i + len(hat) < n:
            y[i:i + len(hat)] += hat
        t += beat
    # onregelmatige fills/uitbarstingen (niet op de beat): maken de juiste verschuiving uniek
    for _ in range(int(dur / 7)):
        tf = rng.uniform(1, dur - 2)
        i = int(tf * sr)
        L = int(rng.uniform(0.15, 0.5) * sr)
        env = np.exp(-np.arange(L) / sr * 6).astype(np.float32)
        y[i:i + L] += (rng.standard_normal(L).astype(np.float32) * 0.35 * env)[: max(0, n - i)]
        events.append((tf, 1.0, "fill"))
    y += rng.standard_normal(n).astype(np.float32) * 0.01
    y = np.clip(y / max(1e-6, np.abs(y).max()) * 0.8, -1, 1)
    tmp = tempfile.mkdtemp(prefix="syncset_")
    wav = os.path.join(tmp, "a.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        s16 = (y * 32767).astype(np.int16)
        w.writeframes(np.repeat(s16[:, None], 2, axis=1).tobytes())
    # beeld
    W, H = 320, 180
    nf = int(dur * a.fps)
    drift = a.drift_ms_per_hour / 1000.0 / 3600.0
    ev_t = np.array([e[0] for e in events]); ev_s = np.array([e[1] for e in events])
    ev_fill = np.array([e[2] == "fill" for e in events])
    vis_t = ev_t + a.lag + drift * ev_t                 # beeld verschijnt later
    order = np.argsort(vis_t); vis_t, ev_s, ev_fill = vis_t[order], ev_s[order], ev_fill[order]
    proc = subprocess.Popen([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}",
                             "-r", f"{a.fps}", "-i", "-", "-i", wav, "-map", "0:v", "-map", "1:a",
                             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
                             "-c:a", "aac", "-b:a", "128k", "-shortest", a.out], stdin=subprocess.PIPE)
    base = np.full((H, W), 30, np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    lamp = np.exp(-(((xx - 60) / 40) ** 2 + ((yy - 40) / 30) ** 2)).astype(np.float32)
    for f in range(nf):
        tf = f / a.fps
        img = base.copy()
        past = vis_t <= tf
        dt = tf - vis_t[past][-12:] if past.any() else np.array([])
        sk = ev_s[past][-12:] if past.any() else np.array([])
        fl = ev_fill[past][-12:] if past.any() else np.array([], bool)
        flash = float(np.sum(sk[~fl] * np.exp(-dt[~fl] / 0.08))) if len(dt) else 0.0
        strobe = float(np.sum(np.exp(-dt[fl] / 0.05))) if len(dt) else 0.0
        img += lamp * 180 * min(1.5, flash)
        img += 90 * min(1.0, strobe)
        bounce = min(1.0, flash)
        y0 = int(110 - 18 * bounce)
        img[y0:y0 + 50, 140:180] = 160
        img += rng.normal(0, 4, img.shape).astype(np.float32)
        proc.stdin.write(np.clip(img, 0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    proc.wait()
    shutil.rmtree(tmp, ignore_errors=True)
    if proc.returncode != 0:
        sys.exit("ffmpeg mislukt")


if __name__ == "__main__":
    main()
