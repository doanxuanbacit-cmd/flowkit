"""edit-beat-sync — music beat grid + rhythmic cut snapping.

Onset strength = positive spectral flux; tempo = autocorrelation peak of the
onset envelope inside 70–180 BPM; beats = phase-aligned grid at that tempo.
Cut points are snapped to the nearest beat only within a tolerance, so
dialogue-driven cuts are never dragged off their natural timing.
"""
from __future__ import annotations

import numpy as np
from pydantic import BaseModel, Field

from studio.editing.audio_io import decode_mono
from studio.editing.skills.base import SkillSpec

SR = 22050
N_FFT = 1024
HOP = 512


class BeatInput(BaseModel):
    music: str
    cut_points: list[float] = Field(default_factory=list)
    tolerance_s: float = Field(0.25, ge=0, le=1)
    min_bpm: float = 70
    max_bpm: float = 180


class BeatOutput(BaseModel):
    bpm: float
    beats: list[float]
    snapped: list[float]
    moved: int


def onset_envelope(x: np.ndarray) -> np.ndarray:
    if len(x) < N_FFT:
        return np.zeros(1, np.float32)
    n = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(N_FFT)[None, :]
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= flux.mean()
    return np.maximum(flux, 0).astype(np.float32)


def estimate_beats(env: np.ndarray, min_bpm: float, max_bpm: float) -> tuple[float, list[float]]:
    fps = SR / HOP
    if env.max() <= 0:
        return 0.0, []
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lo, hi = int(fps * 60 / max_bpm), int(fps * 60 / min_bpm)
    hi = min(hi, len(ac) - 1)
    if hi <= lo:
        return 0.0, []
    period = lo + int(np.argmax(ac[lo:hi + 1]))
    phase = int(np.argmax([env[p::period].sum() for p in range(period)]))
    beats = [round((phase + k * period) / fps, 3) for k in range((len(env) - phase) // period + 1)]
    return round(60.0 * fps / period, 1), beats


def snap(cuts: list[float], beats: list[float], tol: float) -> tuple[list[float], int]:
    if not beats:
        return list(cuts), 0
    arr = np.asarray(beats)
    out, moved = [], 0
    for c in cuts:
        b = float(arr[np.argmin(np.abs(arr - c))])
        if abs(b - c) <= tol and b != c:
            out.append(b)
            moved += 1
        else:
            out.append(c)
    return out, moved


def run(inp: BeatInput) -> BeatOutput:
    env = onset_envelope(decode_mono(inp.music, SR))
    bpm, beats = estimate_beats(env, inp.min_bpm, inp.max_bpm)
    snapped, moved = snap(inp.cut_points, beats, inp.tolerance_s)
    return BeatOutput(bpm=bpm, beats=beats, snapped=snapped, moved=moved)


SPEC = SkillSpec("edit-beat-sync", "beat_grid", "Beat/tempo detection and cut snapping to the music grid",
                 BeatInput, BeatOutput, run)
