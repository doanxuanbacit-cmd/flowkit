"""Small-frame sampling for deterministic visual checks (continuity, colour)."""
from __future__ import annotations

import numpy as np

from studio.editing import ffmpeg


def grab_rgb(path: str, t: float, w: int = 64, h: int = 64) -> np.ndarray:
    raw = ffmpeg.run_bytes(["-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
                            "-vf", f"scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    if len(raw) < w * h * 3:
        raise ffmpeg.FFmpegError(f"no frame at {t:.2f}s in {path}")
    return np.frombuffer(raw[: w * h * 3], dtype=np.uint8).reshape(h, w, 3).astype(np.float32) / 255.0


def stats(img: np.ndarray) -> dict:
    rgb = img.reshape(-1, 3).mean(axis=0)
    luma = float(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2])
    s = float(rgb.sum()) or 1e-6
    return {"luma": luma, "r": float(rgb[0]), "g": float(rgb[1]), "b": float(rgb[2]),
            "chroma": (float(rgb[0] / s), float(rgb[1] / s), float(rgb[2] / s))}


def hist_corr(a: np.ndarray, b: np.ndarray, bins: int = 16) -> float:
    """Mean per-channel histogram correlation in [-1, 1]."""
    cs = []
    for ch in range(3):
        ha, _ = np.histogram(a[..., ch], bins=bins, range=(0, 1))
        hb, _ = np.histogram(b[..., ch], bins=bins, range=(0, 1))
        ha = ha - ha.mean()
        hb = hb - hb.mean()
        den = float(np.sqrt((ha ** 2).sum() * (hb ** 2).sum()))
        cs.append(float((ha * hb).sum()) / den if den else 1.0)
    return float(np.mean(cs))


def sample_stats(path: str, in_s: float, out_s: float, n: int = 5) -> dict:
    span = max(0.01, out_s - in_s)
    imgs = [grab_rgb(path, in_s + span * (k + 0.5) / n) for k in range(n)]
    agg = stats(np.mean(np.stack(imgs), axis=0))
    return agg
