"""PCM decode + envelope helpers for deterministic audio analysis."""
from __future__ import annotations

import numpy as np

from studio.editing import ffmpeg


def decode_mono(path: str, sr: int = 16000, start: float = 0.0, duration: float | None = None) -> np.ndarray:
    args = ["-ss", f"{start:.3f}", "-i", path]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = ffmpeg.run_bytes(args)
    return np.frombuffer(raw, dtype=np.float32).copy()


def envelope(x: np.ndarray, sr: int, hop_s: float = 0.01) -> np.ndarray:
    hop = max(1, int(sr * hop_s))
    n = len(x) // hop
    if n == 0:
        return np.zeros(0, np.float32)
    frames = x[: n * hop].reshape(n, hop)
    return np.sqrt((frames ** 2).mean(axis=1)).astype(np.float32)
