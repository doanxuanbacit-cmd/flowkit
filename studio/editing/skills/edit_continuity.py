"""edit-continuity — boundary-frame checks between adjacent clips.

Deterministic part (always runs): exposure jump, colour-cast jump and histogram
mismatch at every join, stricter when the two clips share characters (same
scene expected). Face / wardrobe identity needs vision: the skill writes one
small contact sheet of boundary frames so an AI reviewer looks at a single JPEG
instead of whole videos (token rule), and accepts its findings back.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from studio.editing import frames
from studio.editing.schemas import QAIssue, Severity, TimelineClip
from studio.editing.skills.base import SkillSpec


class ContinuityInput(BaseModel):
    clips: list[TimelineClip]
    durations: dict[str, float]          # clip_id -> source duration
    luma_jump: float = Field(0.12, gt=0)
    chroma_jump: float = Field(0.05, gt=0)
    min_hist_corr: float = Field(0.35, ge=-1, le=1)
    contact_sheet: str | None = None     # output path for the review sheet
    ai_findings: list[QAIssue] = Field(default_factory=list)


class JoinReport(BaseModel):
    a: str
    b: str
    luma_delta: float
    chroma_delta: float
    hist_corr: float
    same_scene: bool


class ContinuityOutput(BaseModel):
    joins: list[JoinReport]
    issues: list[QAIssue]
    contact_sheet: str | None = None


def _write_sheet(pairs: list[tuple[np.ndarray, np.ndarray]], path: str) -> str:
    from studio.editing import ffmpeg
    rows = [np.concatenate([a, np.ones((a.shape[0], 4, 3), np.float32), b], axis=1) for a, b in pairs]
    sep = np.ones((4, rows[0].shape[1], 3), np.float32)
    sheet = rows[0]
    for r in rows[1:]:
        sheet = np.concatenate([sheet, sep, r], axis=0)
    data = (np.clip(sheet, 0, 1) * 255).astype(np.uint8)
    h, w, _ = data.shape
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    raw = Path(path).with_suffix(".rgb")
    raw.write_bytes(data.tobytes())
    ffmpeg.run(["-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                "-i", str(raw), "-frames:v", "1", path])
    raw.unlink(missing_ok=True)
    return path


def run(inp: ContinuityInput) -> ContinuityOutput:
    joins, issues, pairs = [], [], []
    for a, b in zip(inp.clips, inp.clips[1:]):
        a_out = a.out_s if a.out_s is not None else inp.durations[a.id]
        fa = frames.grab_rgb(a.source, a_out - 0.08, 160, 160)
        fb = frames.grab_rgb(b.source, b.in_s + 0.04, 160, 160)
        sa, sb = frames.stats(fa), frames.stats(fb)
        ld = abs(sa["luma"] - sb["luma"])
        cd = float(max(abs(x - y) for x, y in zip(sa["chroma"], sb["chroma"])))
        hc = frames.hist_corr(fa, fb)
        same = bool(set(a.characters) & set(b.characters))
        joins.append(JoinReport(a=a.id, b=b.id, luma_delta=round(ld, 4), chroma_delta=round(cd, 4),
                                hist_corr=round(hc, 4), same_scene=same))
        pairs.append((fa, fb))
        sev = Severity.warning if same else Severity.info
        if ld > inp.luma_jump:
            issues.append(QAIssue(code="CONT_EXPOSURE_JUMP", severity=sev, clip_id=b.id, skill="edit-continuity",
                                  message=f"exposure jump {ld:.2f} between {a.id} → {b.id}"))
        if cd > inp.chroma_jump:
            issues.append(QAIssue(code="CONT_COLOR_CAST", severity=sev, clip_id=b.id, skill="edit-continuity",
                                  message=f"colour cast shift {cd:.3f} between {a.id} → {b.id}"))
        if same and hc < inp.min_hist_corr:
            issues.append(QAIssue(code="CONT_LOOK_MISMATCH", severity=Severity.warning, clip_id=b.id,
                                  skill="edit-continuity",
                                  message=f"{a.id} → {b.id} share characters but look different (corr {hc:.2f})"))
    sheet = _write_sheet(pairs, inp.contact_sheet) if inp.contact_sheet and pairs else None
    return ContinuityOutput(joins=joins, issues=issues + list(inp.ai_findings), contact_sheet=sheet)


SPEC = SkillSpec("edit-continuity", "continuity_check",
                 "Exposure/colour/look checks at joins + contact sheet for AI face/wardrobe review",
                 ContinuityInput, ContinuityOutput, run)
