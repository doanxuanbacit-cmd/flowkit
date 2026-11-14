"""edit-color-grading — shot matching, exposure, white balance, LUT pipeline.

`run` measures every clip and returns a corrected ColorAdjust per clip so all
shots match a reference (gray-world WB + luma match, clamped so a grade can
never wreck a shot). `color_graph` turns a ColorAdjust into an FFmpeg graph
fragment; it is the only place colour filters are written (single colour slot).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from studio.editing import frames
from studio.editing.ffmpeg import esc_filter_path
from studio.editing.schemas import ColorAdjust, TimelineClip
from studio.editing.skills.base import SkillSpec

MAX_BRIGHT = 0.12
MAX_BAL = 0.25


class ColorInput(BaseModel):
    clips: list[TimelineClip]
    durations: dict[str, float]
    reference: str | None = None     # clip id; default = median-luma clip
    match: bool = True
    strength: float = Field(0.8, ge=0, le=1)


class ClipGrade(BaseModel):
    clip_id: str
    measured: dict
    color: ColorAdjust


class ColorOutput(BaseModel):
    reference: str
    grades: list[ClipGrade]


def validate_lut(path: str) -> None:
    p = Path(path)
    if p.suffix.lower() != ".cube" or not p.exists():
        raise ValueError(f"LUT must be an existing .cube file: {path}")
    head = p.read_text(errors="ignore")[:4000]
    if "LUT_3D_SIZE" not in head:
        raise ValueError(f"not a 3D .cube LUT: {path}")


def color_graph(c: ColorAdjust, src: str, dst: str) -> str:
    """Graph fragment [src] -> [dst]. Identity grade is a null pass-through."""
    chain = []
    if (c.brightness, c.contrast, c.saturation, c.gamma) != (0.0, 1.0, 1.0, 1.0):
        chain.append(f"eq=brightness={c.brightness:.4f}:contrast={c.contrast:.4f}:"
                     f"saturation={c.saturation:.4f}:gamma={c.gamma:.4f}")
    if (c.r, c.g, c.b) != (0.0, 0.0, 0.0):
        chain.append(f"colorbalance=rm={c.r:.4f}:gm={c.g:.4f}:bm={c.b:.4f}")
    base = ",".join(chain) or "null"
    if not c.lut_path:
        return f"[{src}]{base}[{dst}]"
    validate_lut(c.lut_path)
    lut = f"lut3d=file='{esc_filter_path(c.lut_path)}'"
    if c.lut_strength >= 0.999:
        return f"[{src}]{base},{lut}[{dst}]"
    return (f"[{src}]{base},split[{dst}_o][{dst}_l];[{dst}_l]{lut}[{dst}_g];"
            f"[{dst}_o][{dst}_g]blend=all_mode=normal:all_opacity={c.lut_strength:.3f}[{dst}]")


def _clamp(v: float, m: float) -> float:
    return max(-m, min(m, v))


def run(inp: ColorInput) -> ColorOutput:
    measured = {}
    for c in inp.clips:
        out = c.out_s if c.out_s is not None else inp.durations[c.id]
        measured[c.id] = frames.sample_stats(c.source, c.in_s, out, n=4)
    ref = inp.reference
    if ref is None:
        order = sorted(measured, key=lambda k: measured[k]["luma"])
        ref = order[len(order) // 2]
    r = measured[ref]
    grades = []
    for c in inp.clips:
        m = measured[c.id]
        color = c.color.model_copy()
        if inp.match and c.id != ref:
            k = inp.strength
            color.brightness = round(_clamp(color.brightness + (r["luma"] - m["luma"]) * k, MAX_BRIGHT), 4)
            # gray-world: push each channel's share toward the reference share
            for i, ch in enumerate("rgb"):
                delta = (r["chroma"][i] - m["chroma"][i]) * 3.0 * k
                setattr(color, ch, round(_clamp(getattr(color, ch) + delta, MAX_BAL), 4))
        grades.append(ClipGrade(clip_id=c.id, measured={k: v for k, v in m.items() if k != "chroma"}, color=color))
    return ColorOutput(reference=ref, grades=grades)


SPEC = SkillSpec("edit-color-grading", "color", "Shot matching, exposure/WB correction and LUT application",
                 ColorInput, ColorOutput, run)
