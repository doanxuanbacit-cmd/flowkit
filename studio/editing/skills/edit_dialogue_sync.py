"""edit-dialogue-sync — measure and fix audio/picture offset.

Cross-correlates the energy envelope of a clip's embedded audio against a
reference dialogue track (the TTS/voice file that was meant to play) and
returns the offset to apply. Positive offset = reference must be delayed.
Lip movement itself is a vision task; this measures the audio side exactly.
"""
from __future__ import annotations

import numpy as np
from pydantic import BaseModel, Field

from studio.editing.audio_io import decode_mono, envelope
from studio.editing.skills.base import SkillSpec

SR = 8000
HOP = 0.01


class SyncInput(BaseModel):
    clip_audio: str          # media with the audio as it plays in picture
    reference: str           # dialogue file that should line up
    max_offset_s: float = Field(1.0, gt=0, le=5)
    tolerance_ms: float = Field(45.0, ge=0)   # ~1 frame @ 24fps; ITU-R BT.1359 detectability ≈ +45/-125 ms


class SyncOutput(BaseModel):
    offset_ms: float
    confidence: float
    in_sync: bool
    fix_filter: str | None   # apply to the reference stem


def estimate_offset(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[int, float]:
    """Lag (in hops) such that b shifted by lag best matches a."""
    a = (a - a.mean()) / (a.std() + 1e-9)
    b = (b - b.mean()) / (b.std() + 1e-9)
    best, best_v = 0, -np.inf
    n = min(len(a), len(b))
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = a[lag:n], b[: n - lag]
        else:
            x, y = a[: n + lag], b[-lag:n]
        if len(x) < 10:
            continue
        v = float((x * y).mean())
        if v > best_v:
            best, best_v = lag, v
    return best, max(0.0, min(1.0, best_v))


def run(inp: SyncInput) -> SyncOutput:
    ea = envelope(decode_mono(inp.clip_audio, SR), SR, HOP)
    eb = envelope(decode_mono(inp.reference, SR), SR, HOP)
    lag, conf = estimate_offset(ea, eb, int(inp.max_offset_s / HOP))
    off_ms = lag * HOP * 1000.0
    ok = abs(off_ms) <= inp.tolerance_ms
    fix = None
    if not ok:
        fix = f"adelay={int(off_ms)}|{int(off_ms)}" if off_ms > 0 else f"atrim=start={-off_ms / 1000:.3f},asetpts=PTS-STARTPTS"
    return SyncOutput(offset_ms=round(off_ms, 1), confidence=round(conf, 3), in_sync=ok, fix_filter=fix)


SPEC = SkillSpec("edit-dialogue-sync", "av_sync", "Audio offset measurement and correction vs reference dialogue",
                 SyncInput, SyncOutput, run)
