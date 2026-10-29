"""Geheugenzuinige audio-extractie voor lange sets.

FFmpeg decodeert de audio naar mono 22,05 kHz en streamt die in blokken (±60 s) naar numpy.
Per 0,1 s bewaren we slechts 7 getallen; een set van 3 uur is dus ± 3 MB, nooit een volledige
spectrale matrix. Het resultaat wordt gecachet zodat heropenen direct gaat."""
from __future__ import annotations
import hashlib
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .ffmpeg_tools import ToolError
from .platform_paths import cache_dir as platform_cache_dir, no_window_kwargs

SR = 22050
WIN = 4096
HOP = 2205                 # 0,1 s
FPS = SR / HOP             # 10 frames per seconde
COLS = ["peak", "rms_db", "bass_db", "lowmid_db", "mid_db", "high_db", "flux"]


@dataclass
class Features:
    data: np.ndarray       # (n, 7) float32, kolommen zie COLS
    duration: float

    @property
    def n(self) -> int:
        return self.data.shape[0]

    @property
    def peak(self) -> np.ndarray:
        return self.data[:, 0]

    def col(self, name: str) -> np.ndarray:
        return self.data[:, COLS.index(name)]


def cache_dir() -> str:
    base = str(platform_cache_dir())     # os.uname() bestaat niet op Windows
    os.makedirs(base, exist_ok=True)
    return base


def cache_path(path: str) -> str:
    st = os.stat(path)
    key = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|v1"
    return os.path.join(cache_dir(), hashlib.sha1(key.encode("utf-8")).hexdigest() + ".npz")


def load_cached(path: str) -> Features | None:
    try:
        cp = cache_path(path)
        if os.path.isfile(cp):
            z = np.load(cp)
            return Features(z["data"], float(z["duration"]))
    except Exception:
        return None
    return None


def _band(freqs, lo, hi):
    return np.where((freqs >= lo) & (freqs < hi))[0]


def extract(ffmpeg: str, path: str, duration: float,
            progress: Callable[[float], None] | None = None,
            cancel: threading.Event | None = None, use_cache: bool = True) -> Features:
    if use_cache:
        c = load_cached(path)
        if c is not None:
            return c
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0", "-vn", "-ac", "1",
           "-ar", str(SR), "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs())
    freqs = np.fft.rfftfreq(WIN, 1.0 / SR)
    b_bass, b_lowmid = _band(freqs, 30, 150), _band(freqs, 150, 500)
    b_mid, b_high = _band(freqs, 500, 2000), _band(freqs, 2000, 8000)
    b_flux = _band(freqs, 30, 8000)
    window = np.hanning(WIN).astype(np.float32)
    eps = 1e-10
    chunk_bytes = SR * 60 * 4
    pending = np.zeros(0, dtype=np.float32)
    prev_logmag: np.ndarray | None = None
    out_blocks: list[np.ndarray] = []
    total_samples = 0
    leftover = b""
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise _Cancel()
            raw = proc.stdout.read(chunk_bytes)  # type: ignore[union-attr]
            if not raw:
                break
            raw = leftover + raw
            usable = len(raw) - (len(raw) % 4)
            leftover = raw[usable:]
            pending = np.concatenate([pending, np.frombuffer(raw[:usable], dtype=np.float32)])
            total_samples += usable // 4
            n_frames = (len(pending) - WIN) // HOP + 1 if len(pending) >= WIN else 0
            if n_frames <= 0:
                continue
            seg = np.lib.stride_tricks.sliding_window_view(pending, WIN)[::HOP][:n_frames]
            peak = np.abs(seg).max(axis=1)
            seg_w = seg * window
            mag = np.abs(np.fft.rfft(seg_w, axis=1)).astype(np.float32)
            pw = mag ** 2
            rms = np.sqrt((seg ** 2).mean(axis=1) + eps)
            blk = np.empty((n_frames, len(COLS)), dtype=np.float32)
            blk[:, 0] = peak
            blk[:, 1] = 20 * np.log10(rms)
            for j, b in enumerate((b_bass, b_lowmid, b_mid, b_high), start=2):
                blk[:, j] = 10 * np.log10(pw[:, b].sum(axis=1) + eps)
            logmag = np.log1p(mag[:, b_flux] * 50.0)
            if prev_logmag is None:
                prev_row = logmag[:1]
            else:
                prev_row = prev_logmag
            shifted = np.vstack([prev_row, logmag[:-1]])
            blk[:, 6] = np.maximum(logmag - shifted, 0).mean(axis=1)
            prev_logmag = logmag[-1:]
            out_blocks.append(blk)
            pending = pending[n_frames * HOP:]
            if progress and duration > 0:
                progress(min(0.99, total_samples / SR / duration))
    except _Cancel:
        proc.kill()
        raise
    finally:
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        except Exception:
            pass
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""  # type: ignore[union-attr]
    proc.wait()
    if proc.stderr:
        proc.stderr.close()
    if cancel is not None and cancel.is_set():
        raise _Cancel()
    if not out_blocks:
        if "does not contain any stream" in err or "matches no streams" in err or total_samples == 0:
            raise ToolError("In deze video is geen audio gevonden om te analyseren. "
                            "Je kunt wel handmatig clips maken.")
        raise ToolError("De audio kon niet worden gelezen.\n" + err[-300:])
    data = np.vstack(out_blocks)
    feats = Features(data, total_samples / SR)
    if use_cache:
        try:
            tmp = cache_path(path) + ".tmp.npz"
            np.savez_compressed(tmp, data=data, duration=feats.duration)
            os.replace(tmp, cache_path(path))
        except OSError:
            pass
    if progress:
        progress(1.0)
    return feats


class _Cancel(Exception):
    pass


def is_cancel(e: BaseException) -> bool:
    return isinstance(e, _Cancel)
