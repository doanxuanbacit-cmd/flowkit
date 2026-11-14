"""edit-auto-subtitles — Vietnamese ASR / script alignment, SRT + ASS, safe zones.

Sources, in order of preference:
1. `cues` given (already timed, e.g. from the storyboard) — formatted only.
2. `script_lines` + audio — aligned to detected speech regions (no AI tokens).
3. ASR via faster-whisper (`language="vi"`) when the package is installed.
   It is optional; without it the skill says so instead of inventing text.
"""
from __future__ import annotations

import os
import re
import textwrap
from pathlib import Path

from pydantic import BaseModel, Field

from studio.editing.ffmpeg import esc_filter_path
from studio.editing.schemas import SubtitleCue, SubtitleStyle
from studio.editing.skills.base import SkillSpec
from studio.editing.skills.edit_smart_cut import detect_silences


class SubtitleInput(BaseModel):
    out_dir: str
    basename: str = "subtitles"
    width: int = 1080
    height: int = 1920
    duration: float = Field(gt=0)
    style: SubtitleStyle = SubtitleStyle()
    cues: list[SubtitleCue] = Field(default_factory=list)
    script_lines: list[str] = Field(default_factory=list)
    audio: str | None = None
    use_asr: bool = False
    asr_model: str = "small"


class SubtitleOutput(BaseModel):
    cues: list[SubtitleCue]
    srt: str
    ass: str
    source: str
    warnings: list[str] = []


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000:d}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def wrap(text: str, max_chars: int) -> list[str]:
    return textwrap.wrap(" ".join(text.split()), width=max_chars, break_long_words=False) or [""]


def segment(cues: list[SubtitleCue], style: SubtitleStyle) -> list[SubtitleCue]:
    """Split cues whose wrapped text exceeds max_lines into timed chunks."""
    out = []
    for c in cues:
        lines = wrap(c.text, style.max_chars_per_line)
        if len(lines) <= style.max_lines:
            out.append(SubtitleCue(start=c.start, end=c.end, text="\n".join(lines), speaker=c.speaker))
            continue
        chunks = ["\n".join(lines[i:i + style.max_lines]) for i in range(0, len(lines), style.max_lines)]
        total = sum(len(x) for x in chunks) or 1
        t = c.start
        for ch in chunks:
            d = (c.end - c.start) * len(ch) / total
            out.append(SubtitleCue(start=round(t, 3), end=round(t + d, 3), text=ch, speaker=c.speaker))
            t += d
    return out


def speech_regions(audio: str, duration: float) -> list[tuple[float, float]]:
    sil = detect_silences(audio, -35.0, 0.35, duration)
    regions, cur = [], 0.0
    for s in sil:
        if s.start - cur > 0.15:
            regions.append((cur, s.start))
        cur = s.end
    if duration - cur > 0.15:
        regions.append((cur, duration))
    return regions


def align_script(lines: list[str], regions: list[tuple[float, float]], duration: float) -> list[SubtitleCue]:
    """Spread script lines across speech time, proportional to character count."""
    lines = [ln.strip() for ln in lines if ln.strip()]
    if not lines:
        return []
    if not regions:
        regions = [(0.0, duration)]
    speech = sum(e - s for s, e in regions)
    total_chars = sum(len(ln) for ln in lines)
    # map "speech-time" positions back to the absolute timeline
    def at(pos: float) -> float:
        acc = 0.0
        for s, e in regions:
            if pos <= acc + (e - s):
                return s + (pos - acc)
            acc += e - s
        return regions[-1][1]
    cues, p = [], 0.0
    for ln in lines:
        d = speech * len(ln) / total_chars
        st, en = at(p), at(p + d)
        if en - st < 0.4:
            en = min(duration, st + 0.4)
        cues.append(SubtitleCue(start=round(st, 3), end=round(en, 3), text=ln))
        p += d
    return cues


def asr(audio: str, model: str) -> list[SubtitleCue]:
    from faster_whisper import WhisperModel  # optional dependency
    m = WhisperModel(model, device=os.environ.get("STUDIO_ASR_DEVICE", "cpu"), compute_type="int8")
    segs, _ = m.transcribe(audio, language="vi", vad_filter=True, word_timestamps=True)
    return [SubtitleCue(start=round(s.start, 3), end=round(max(s.end, s.start + 0.3), 3), text=s.text.strip())
            for s in segs if s.text.strip()]


def to_srt(cues: list[SubtitleCue]) -> str:
    return "\n".join(f"{i}\n{_ts_srt(c.start)} --> {_ts_srt(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1))


def to_ass(cues: list[SubtitleCue], style: SubtitleStyle, w: int, h: int) -> str:
    size = int(style.font_size * w / 1080)
    margin_v = int(style.margin_v_pct * h)
    margin_lr = int(0.06 * w)
    font = os.environ.get("STUDIO_SUB_FONT", "DejaVu Sans")
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},{style.primary},&H000000FF,{style.outline},&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{style.outline_w:g},1,2,{margin_lr},{margin_lr},{margin_v},1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = []
    for c in cues:
        txt = c.text.replace("\n", r"\N").replace("{", "(").replace("}", ")")
        ev.append(f"Dialogue: 0,{_ts_ass(c.start)},{_ts_ass(c.end)},Default,{c.speaker or ''},0,0,0,,{txt}")
    return head + "\n".join(ev) + "\n"


def burn_filter(ass_path: str) -> str:
    return f"subtitles=filename='{esc_filter_path(ass_path)}'"


def run(inp: SubtitleInput) -> SubtitleOutput:
    warnings: list[str] = []
    if inp.cues:
        cues, source = inp.cues, "provided"
    elif inp.script_lines and inp.audio:
        cues, source = align_script(inp.script_lines, speech_regions(inp.audio, inp.duration), inp.duration), "script_aligned"
    elif inp.script_lines:
        cues, source = align_script(inp.script_lines, [], inp.duration), "script_even"
        warnings.append("no audio given: script spread evenly — check timing")
    elif inp.use_asr and inp.audio:
        try:
            cues, source = asr(inp.audio, inp.asr_model), "asr_faster_whisper"
        except ImportError:
            cues, source = [], "none"
            warnings.append("ASR requested but faster-whisper is not installed (pip install faster-whisper)")
    else:
        cues, source = [], "none"
    cues = [c for c in cues if c.start < inp.duration]
    for c in cues:
        if c.end > inp.duration:
            c.end = round(inp.duration, 3)
    cues = segment(cues, inp.style)
    for a, b in zip(cues, cues[1:]):
        if b.start < a.end:
            a.end = max(a.start + 0.2, b.start - 0.02)
    out = Path(inp.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    srt = out / f"{inp.basename}.srt"
    ass = out / f"{inp.basename}.ass"
    srt.write_text(to_srt(cues), encoding="utf-8")
    ass.write_text(to_ass(cues, inp.style, inp.width, inp.height), encoding="utf-8")
    if any(re.search(r"\S{%d,}" % (inp.style.max_chars_per_line + 1), c.text) for c in cues):
        warnings.append("a single word is longer than a subtitle line")
    return SubtitleOutput(cues=cues, srt=str(srt), ass=str(ass), source=source, warnings=warnings)


SPEC = SkillSpec("edit-auto-subtitles", "subtitles", "Vietnamese subtitles: ASR/script alignment, SRT+ASS, safe zones",
                 SubtitleInput, SubtitleOutput, run)
