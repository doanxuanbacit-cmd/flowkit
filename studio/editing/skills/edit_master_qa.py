"""edit-master-qa — final technical QA of a rendered file.

One FFmpeg analysis pass (blackdetect + freezedetect + silencedetect + astats)
plus ffprobe export validation, loudness check and subtitle-overflow check.
Every issue carries a time range and, when the timeline map is given, the
clip_id it falls in — so the engine can re-render only that clip.
"""
from __future__ import annotations

import re
import subprocess

from pydantic import BaseModel, Field

from studio.editing import ffmpeg
from studio.editing.schemas import QAIssue, QAReport, Severity, SubtitleCue, SubtitleStyle
from studio.editing.skills.base import SkillSpec
from studio.editing.skills.edit_audio_master import measure_loudness


class ClipSpan(BaseModel):
    clip_id: str
    start: float
    end: float


class QAInput(BaseModel):
    path: str
    expect_width: int | None = None
    expect_height: int | None = None
    expect_fps: float | None = None
    expect_audio: bool = True
    target_lufs: float | None = -14.0
    lufs_tolerance: float = 1.5
    max_true_peak: float = -1.0
    black_min_s: float = 0.25
    freeze_min_s: float = 2.0
    silence_min_s: float = 2.5
    subtitles: list[SubtitleCue] = Field(default_factory=list)
    subtitle_style: SubtitleStyle | None = None
    clip_map: list[ClipSpan] = Field(default_factory=list)


def _clip_at(t: float | None, spans: list[ClipSpan]) -> str | None:
    if t is None:
        return None
    for s in spans:
        if s.start <= t < s.end:
            return s.clip_id
    return spans[-1].clip_id if spans and t >= spans[-1].start else None


def _pairs(stderr: str, a: str, b: str) -> list[tuple[float, float | None]]:
    starts = [float(x) for x in re.findall(rf"{a}[:=]\s*(-?\d+(?:\.\d+)?)", stderr)]
    ends = [float(x) for x in re.findall(rf"{b}[:=]\s*(-?\d+(?:\.\d+)?)", stderr)]
    return [(s, ends[i] if i < len(ends) else None) for i, s in enumerate(starts)]


def subtitle_issues(cues: list[SubtitleCue], style: SubtitleStyle, width: int, duration: float) -> list[QAIssue]:
    issues = []
    size = style.font_size * width / 1080
    usable = width * 0.88
    for i, c in enumerate(cues):
        lines = c.text.split("\n")
        if len(lines) > style.max_lines:
            issues.append(QAIssue(code="SUB_TOO_MANY_LINES", severity=Severity.error, start=c.start, end=c.end,
                                  skill="edit-master-qa", message=f"cue {i + 1} has {len(lines)} lines"))
        widest = max(len(x) for x in lines)
        if widest * size * 0.55 > usable:
            issues.append(QAIssue(code="SUB_OVERFLOW", severity=Severity.error, start=c.start, end=c.end,
                                  skill="edit-master-qa", message=f"cue {i + 1} line too wide ({widest} chars)"))
        if c.end > duration + 0.05:
            issues.append(QAIssue(code="SUB_PAST_END", severity=Severity.warning, start=c.start, end=c.end,
                                  skill="edit-master-qa", message=f"cue {i + 1} ends after the video"))
        if (c.end - c.start) < 0.5:
            issues.append(QAIssue(code="SUB_TOO_FAST", severity=Severity.warning, start=c.start, end=c.end,
                                  skill="edit-master-qa", message=f"cue {i + 1} on screen < 0.5s"))
    for i, (a, b) in enumerate(zip(cues, cues[1:])):
        if b.start < a.end - 0.01:
            issues.append(QAIssue(code="SUB_OVERLAP", severity=Severity.warning, start=b.start, end=a.end,
                                  skill="edit-master-qa", message=f"cues {i + 1} and {i + 2} overlap"))
    return issues


def run(inp: QAInput) -> QAReport:
    info = ffmpeg.probe(inp.path)
    issues: list[QAIssue] = []
    metrics: dict = {"duration": info.duration, "width": info.width, "height": info.height, "fps": info.fps,
                     "video_codec": info.video_codec, "pix_fmt": info.pix_fmt, "has_audio": info.has_audio}

    def add(code, sev, msg, start=None, end=None):
        issues.append(QAIssue(code=code, severity=sev, message=msg, start=start, end=end,
                              clip_id=_clip_at(start, inp.clip_map), skill="edit-master-qa"))

    # export validation
    if info.video_codec != "h264":
        add("EXPORT_CODEC", Severity.error, f"video codec {info.video_codec}, expected h264")
    if info.pix_fmt != "yuv420p":
        add("EXPORT_PIXFMT", Severity.error, f"pix_fmt {info.pix_fmt}, expected yuv420p")
    if inp.expect_width and (info.width, info.height) != (inp.expect_width, inp.expect_height):
        add("EXPORT_SIZE", Severity.error, f"{info.width}x{info.height}, expected {inp.expect_width}x{inp.expect_height}")
    if inp.expect_fps and abs(info.fps - inp.expect_fps) > 0.1:
        add("EXPORT_FPS", Severity.warning, f"{info.fps} fps, expected {inp.expect_fps}")
    if inp.expect_audio and not info.has_audio:
        add("AUDIO_MISSING", Severity.error, "no audio stream")
    if info.width and info.width % 2 or info.height % 2:
        add("EXPORT_ODD_DIM", Severity.error, "odd frame dimension")

    args = ["-loglevel", "info", "-i", inp.path,
            "-vf", f"blackdetect=d={inp.black_min_s}:pix_th=0.10,freezedetect=n=-60dB:d={inp.freeze_min_s}"]
    if info.has_audio:
        args += ["-af", f"silencedetect=noise=-50dB:d={inp.silence_min_s},astats=metadata=0:measure_perchannel=none"]
    res = ffmpeg.run(args + ["-f", "null", "-"], check=False)
    err = res.stderr
    for s, e in _pairs(err, "black_start", "black_end"):
        edge = s < 0.05 or (e is not None and e > info.duration - 0.05)
        add("BLACK_FRAMES", Severity.warning if edge else Severity.error,
            f"black {s:.2f}–{(e or info.duration):.2f}s", s, e)
    for s, e in _pairs(err, "lavfi.freezedetect.freeze_start", "lavfi.freezedetect.freeze_end"):
        add("FREEZE", Severity.warning, f"frozen picture {s:.2f}–{(e or info.duration):.2f}s", s, e)
    if info.has_audio:
        for s, e in _pairs(err, "silence_start", "silence_end"):
            add("LONG_SILENCE", Severity.warning, f"silence {s:.2f}–{(e or info.duration):.2f}s", s, e)
        peaks = re.findall(r"Peak level dB:\s*(-?(?:inf|\d+(?:\.\d+)?))", err)
        if peaks:
            pk = float(peaks[-1]) if peaks[-1] not in ("-inf", "inf") else -120.0
            metrics["peak_db"] = pk
            if pk >= -0.1:
                add("AUDIO_CLIPPING", Severity.error, f"sample peak {pk:.2f} dBFS")
        try:
            loud = measure_loudness(inp.path)
            metrics["lufs"] = float(loud["input_i"])
            metrics["true_peak"] = float(loud["input_tp"])
            if inp.target_lufs is not None and metrics["lufs"] > -70:
                d = abs(metrics["lufs"] - inp.target_lufs)
                if d > inp.lufs_tolerance:
                    add("LOUDNESS", Severity.error if d > 3 else Severity.warning,
                        f"{metrics['lufs']:.1f} LUFS vs target {inp.target_lufs}")
            if metrics["true_peak"] > inp.max_true_peak:
                add("TRUE_PEAK", Severity.warning, f"true peak {metrics['true_peak']:.1f} dBTP")
        except (ffmpeg.FFmpegError, ValueError, KeyError):
            add("LOUDNESS_UNMEASURED", Severity.warning, "could not measure loudness")
        # A/V drift: stream durations
        try:
            out = subprocess.run([ffmpeg.ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type,duration",
                                         "-of", "csv=p=0", inp.path], capture_output=True, text=True, timeout=30).stdout
            durs = {}
            for line in out.strip().splitlines():
                kind, _, d = line.partition(",")
                if d and d != "N/A":
                    durs[kind] = float(d)
            if "video" in durs and "audio" in durs:
                drift = abs(durs["video"] - durs["audio"])
                metrics["av_duration_delta"] = round(drift, 3)
                if drift > 0.2:
                    add("AV_LENGTH_MISMATCH", Severity.warning, f"audio/video lengths differ by {drift:.2f}s")
        except Exception:  # noqa: BLE001 - metric only
            pass
    if inp.subtitles and inp.subtitle_style:
        issues += subtitle_issues(inp.subtitles, inp.subtitle_style, info.width or 1080, info.duration)
    passed = not any(i.severity == Severity.error for i in issues)
    return QAReport(target=inp.path, passed=passed, issues=issues, metrics=metrics)


SPEC = SkillSpec("edit-master-qa", "final_qa",
                 "Black frames, freezes, clipping, loudness, A/V length, subtitle overflow, export validation",
                 QAInput, QAReport, run)
