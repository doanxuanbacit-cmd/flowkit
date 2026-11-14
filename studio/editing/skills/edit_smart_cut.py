"""edit-smart-cut — scene detection, silence removal, action/motion cut points.

Pure FFmpeg analysis (scdet + silencedetect); no AI tokens. Output is a list of
keep-segments and cut points the editor can apply to the timeline.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from studio.editing import ffmpeg
from studio.editing.skills.base import SkillSpec


class SmartCutInput(BaseModel):
    path: str
    scene_threshold: float = Field(10.0, ge=1, le=100)
    silence_db: float = Field(-35.0, le=0)
    min_silence_s: float = Field(0.6, ge=0.1)
    pad_s: float = Field(0.12, ge=0)       # breathing room kept around speech
    remove_internal_silence: bool = False  # dialogue films keep natural pauses by default


class Segment(BaseModel):
    start: float
    end: float


class SmartCutOutput(BaseModel):
    duration: float
    scene_changes: list[float]
    silences: list[Segment]
    keep: list[Segment]
    trim_in: float
    trim_out: float
    cut_points: list[float]   # good places to cut: scene changes + silence midpoints


def detect_scenes(path: str, threshold: float) -> list[float]:
    res = ffmpeg.run(["-loglevel", "info", "-i", path, "-an", "-vf", f"scdet=threshold={threshold}",
                      "-f", "null", "-"], check=False)
    return sorted({round(float(t), 3) for t in re.findall(r"lavfi\.scd\.time:\s*(\d+(?:\.\d+)?)", res.stderr)})


def detect_silences(path: str, noise_db: float, min_s: float, duration: float) -> list[Segment]:
    res = ffmpeg.run(["-loglevel", "info", "-i", path, "-vn",
                      "-af", f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"], check=False)
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?\d+(?:\.\d+)?)", res.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?\d+(?:\.\d+)?)", res.stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        out.append(Segment(start=max(0.0, round(s, 3)), end=round(min(e, duration), 3)))
    return out


def run(inp: SmartCutInput) -> SmartCutOutput:
    info = ffmpeg.probe(inp.path)
    dur = info.duration
    scenes = detect_scenes(inp.path, inp.scene_threshold) if info.width else []
    sil = detect_silences(inp.path, inp.silence_db, inp.min_silence_s, dur) if info.has_audio else []
    trim_in, trim_out = 0.0, dur
    for s in sil:
        if s.start <= 0.05:
            trim_in = max(0.0, s.end - inp.pad_s)
        if s.end >= dur - 0.05:
            trim_out = min(dur, s.start + inp.pad_s)
    if trim_out <= trim_in:  # all silent (e.g. b-roll): keep everything
        trim_in, trim_out = 0.0, dur
    keep: list[Segment] = []
    cursor = trim_in
    if inp.remove_internal_silence:
        for s in sil:
            if s.start <= trim_in or s.end >= trim_out:
                continue
            if s.start + inp.pad_s > cursor:
                keep.append(Segment(start=round(cursor, 3), end=round(s.start + inp.pad_s, 3)))
            cursor = max(cursor, s.end - inp.pad_s)
    keep.append(Segment(start=round(cursor, 3), end=round(trim_out, 3)))
    mids = [round((s.start + s.end) / 2, 3) for s in sil if trim_in < s.start and s.end < trim_out]
    cuts = sorted(set(scenes + mids))
    return SmartCutOutput(duration=dur, scene_changes=scenes, silences=sil, keep=keep,
                          trim_in=round(trim_in, 3), trim_out=round(trim_out, 3), cut_points=cuts)


SPEC = SkillSpec("edit-smart-cut", "cut_points", "Scene detection, silence trim, cut-point suggestions",
                 SmartCutInput, SmartCutOutput, run)
