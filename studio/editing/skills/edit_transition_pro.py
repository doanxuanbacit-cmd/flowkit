"""edit-transition-pro — builds the FFmpeg graph that joins clips.

Picture: hard cut (concat), crossfade/fade-to-black/whip-pan (xfade).
Sound: every clip's audio is placed on an absolute timeline (adelay + amix), so
J-cuts (incoming sound leads) and L-cuts (outgoing sound trails) are just audio
start offsets with handles — no picture tricks needed.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from studio.editing.schemas import Transition, TransitionType
from studio.editing.skills.base import SkillSpec

XFADE = {
    TransitionType.crossfade: "fade",
    TransitionType.fade_black: "fadeblack",
    TransitionType.whip_pan: "smoothleft",
}
MIN_FADE = 0.06


class JoinClip(BaseModel):
    video_input: int
    audio_input: int | None = None   # None = silent clip
    duration: float = Field(gt=0)
    audio_head: float = Field(0.0, ge=0)   # extra audio before picture in-point (J-cut handle)
    audio_tail: float = Field(0.0, ge=0)   # extra audio after picture out-point (L-cut handle)
    transition_in: Transition = Field(default_factory=Transition)


class TransitionInput(BaseModel):
    clips: list[JoinClip]
    fps: float = 30.0
    sample_rate: int = 48000


class TransitionOutput(BaseModel):
    filter_complex: str
    video_label: str
    audio_label: str | None
    total_duration: float
    starts: list[float]
    warnings: list[str] = []


def run(inp: TransitionInput) -> TransitionOutput:
    parts: list[str] = []
    warnings: list[str] = []
    starts: list[float] = []
    clips = inp.clips
    for i, c in enumerate(clips):
        parts.append(f"[{c.video_input}:v]fps={inp.fps:g},settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
    acc = "v0"
    length = clips[0].duration
    starts.append(0.0)
    for i in range(1, len(clips)):
        c = clips[i]
        t = c.transition_in
        out = f"vj{i}"
        if t.is_overlap:
            d = min(t.duration, length - MIN_FADE, c.duration - MIN_FADE)
            if d < t.duration:
                warnings.append(f"clip {i}: transition shortened to {d:.2f}s (clip too short)")
            if d <= MIN_FADE:
                parts.append(f"[{acc}][v{i}]concat=n=2:v=1:a=0,settb=AVTB[{out}]")
                starts.append(length)
                length += c.duration
            else:
                offset = length - d
                parts.append(f"[{acc}][v{i}]xfade=transition={XFADE[t.type]}:duration={d:.3f}:offset={offset:.3f},settb=AVTB[{out}]")
                starts.append(offset)
                length = offset + c.duration
        else:
            parts.append(f"[{acc}][v{i}]concat=n=2:v=1:a=0,settb=AVTB[{out}]")
            starts.append(length)
            length += c.duration
        acc = out
    parts.append(f"[{acc}]trim=duration={length:.3f},setpts=PTS-STARTPTS[vout]")

    # ---- audio -------------------------------------------------------------
    alabels: list[str] = []
    for i, c in enumerate(clips):
        if c.audio_input is None:
            continue
        t_in = c.transition_in
        nxt = clips[i + 1].transition_in if i + 1 < len(clips) else None
        head = c.audio_head if (i > 0 and t_in.type == TransitionType.j_cut) else 0.0
        tail = c.audio_tail if (nxt is not None and nxt.type == TransitionType.l_cut) else 0.0
        seg_len = c.duration + head + tail
        chain = [f"[{c.audio_input}:a]aresample={inp.sample_rate},aformat=channel_layouts=stereo,"
                 f"asetpts=PTS-STARTPTS,atrim=duration={seg_len:.3f}"]
        if i > 0:
            if t_in.is_overlap:
                fin = min(t_in.duration, seg_len / 2)
            elif t_in.type == TransitionType.j_cut and head > 0:
                fin = head
            else:
                fin = MIN_FADE
            chain.append(f"afade=t=in:d={fin:.3f}")
        if nxt is not None:
            if nxt.is_overlap:
                fout = min(nxt.duration, seg_len / 2)
            elif nxt.type == TransitionType.l_cut and tail > 0:
                fout = tail
            else:
                fout = MIN_FADE
            chain.append(f"afade=t=out:st={max(0.0, seg_len - fout):.3f}:d={fout:.3f}")
        start = max(0.0, starts[i] - head)
        ms = int(round(start * 1000))
        chain.append(f"adelay={ms}|{ms}")
        parts.append(",".join(chain) + f"[a{i}]")
        alabels.append(f"[a{i}]")
    alabel = None
    if alabels:
        if len(alabels) == 1:
            parts.append(f"{alabels[0]}apad,atrim=duration={length:.3f}[aout]")
        else:
            parts.append(f"{''.join(alabels)}amix=inputs={len(alabels)}:normalize=0:duration=longest,"
                         f"apad,atrim=duration={length:.3f}[aout]")
        alabel = "aout"
    return TransitionOutput(filter_complex=";".join(parts), video_label="vout", audio_label=alabel,
                            total_duration=round(length, 3), starts=[round(s, 3) for s in starts],
                            warnings=warnings)


SPEC = SkillSpec("edit-transition-pro", "transitions",
                 "Cut / crossfade / fade-black / whip-pan picture joins and J/L-cut audio",
                 TransitionInput, TransitionOutput, run)
