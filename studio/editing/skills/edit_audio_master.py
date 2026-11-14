"""edit-audio-master — dialogue cleanup, ducking, two-pass loudness normalisation.

`mix_graph` builds the stem graph (dialogue clean-up chain, music ducked under
dialogue with sidechain compression, SFX bus). `run` measures the pre-master
and returns the exact second-pass loudnorm filter (EBU R128, linear mode), so
loudness is normalised once, at the end, and nowhere else.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel

from studio.editing import ffmpeg
from studio.editing.schemas import AudioMasterSpec
from studio.editing.skills.base import SkillSpec


def dialogue_chain(spec: AudioMasterSpec) -> str:
    f = ["highpass=f=80"]
    if spec.denoise:
        f.append(f"afftdn=nr={spec.denoise_nr:g}:nf=-40")
    if spec.voice_eq:
        f += ["equalizer=f=250:t=q:w=1:g=-2", "equalizer=f=3500:t=q:w=1.2:g=2.5"]
    if spec.compress:
        f.append("acompressor=threshold=0.089:ratio=3:attack=15:release=180:makeup=1.5")
    return ",".join(f)


def duck_ratio(duck_db: float) -> float:
    return max(1.0, min(20.0, round(1.0 + abs(duck_db) * 0.6, 2)))


def mix_graph(spec: AudioMasterSpec, dialogue: str | None, music: str | None, sfx: str | None, out: str) -> str:
    """Labels in, one stereo label out. Any stem may be None."""
    parts, bus = [], []
    if dialogue:
        parts.append(f"[{dialogue}]{dialogue_chain(spec)}[dlg_c]")
        if music:
            parts.append("[dlg_c]asplit=2[dlg_mix][dlg_sc]")
            parts.append(f"[{music}][dlg_sc]sidechaincompress=threshold=0.02:ratio={duck_ratio(spec.duck_music_db)}"
                         f":attack=20:release=400:makeup=1[mus_d]")
            bus += ["[dlg_mix]", "[mus_d]"]
        else:
            bus.append("[dlg_c]")
    elif music:
        bus.append(f"[{music}]")
    if sfx:
        bus.append(f"[{sfx}]")
    if not bus:
        raise ValueError("no audio stems")
    if len(bus) == 1:
        parts.append(f"{bus[0]}anull[{out}]")
    else:
        parts.append(f"{''.join(bus)}amix=inputs={len(bus)}:normalize=0:duration=longest[{out}]")
    return ";".join(parts)


def measure_loudness(path: str, spec: AudioMasterSpec | None = None) -> dict:
    spec = spec or AudioMasterSpec()
    res = ffmpeg.run(["-loglevel", "info", "-i", path, "-vn", "-af",
                      f"loudnorm=I={spec.target_lufs}:TP={spec.true_peak}:LRA=11:print_format=json",
                      "-f", "null", "-"], check=True)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr, re.S)
    if not m:
        raise ffmpeg.FFmpegError("loudnorm produced no measurement", res.stderr)
    return json.loads(m[-1])


class AudioMasterInput(BaseModel):
    premaster: str
    spec: AudioMasterSpec = AudioMasterSpec()


class AudioMasterOutput(BaseModel):
    measured: dict
    loudnorm_filter: str
    silent: bool


def run(inp: AudioMasterInput) -> AudioMasterOutput:
    m = measure_loudness(inp.premaster, inp.spec)
    s = inp.spec
    try:
        ii = float(m["input_i"])
    except (TypeError, ValueError):
        ii = float("-inf")
    if ii == float("-inf") or ii < -70:
        return AudioMasterOutput(measured=m, loudnorm_filter="anull", silent=True)
    f = (f"loudnorm=I={s.target_lufs}:TP={s.true_peak}:LRA=11:measured_I={m['input_i']}:"
         f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
         f"offset={m['target_offset']}:linear=true,aresample=48000")
    return AudioMasterOutput(measured=m, loudnorm_filter=f, silent=False)


SPEC = SkillSpec("edit-audio-master", "audio_master",
                 "Noise reduction, EQ, compression, ducking, two-pass EBU R128 loudness",
                 AudioMasterInput, AudioMasterOutput, run)
