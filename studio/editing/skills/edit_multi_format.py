"""edit-multi-format — 9:16 / 16:9 / 1:1 / 4:5 renditions with platform presets.

Never claims more quality than the source has: if the source is smaller than
the target frame the output is flagged `upscaled=True` (and the manifest says
so) instead of being labelled 1080p/4K silently.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from studio.editing.schemas import ExportFormat, Platform
from studio.editing.skills.base import SkillSpec


class Preset(BaseModel):
    platform: Platform
    fmt: ExportFormat
    width: int
    height: int
    fps_max: float
    video_bitrate: str
    max_duration_s: float | None
    min_duration_s: float | None = None
    target_lufs: float = -14.0
    note: str = ""


# Durations reflect the platforms' published limits as checked when this was
# written; the publishers re-validate against the live API response.
PRESETS: dict[Platform, Preset] = {
    Platform.master: Preset(platform=Platform.master, fmt=ExportFormat.vertical, width=0, height=0, fps_max=60,
                            video_bitrate="", max_duration_s=None, note="timeline resolution, CRF 16"),
    Platform.tiktok: Preset(platform=Platform.tiktok, fmt=ExportFormat.vertical, width=1080, height=1920, fps_max=60,
                            video_bitrate="10M", max_duration_s=600, min_duration_s=3),
    Platform.youtube_shorts: Preset(platform=Platform.youtube_shorts, fmt=ExportFormat.vertical, width=1080,
                                    height=1920, fps_max=60, video_bitrate="12M", max_duration_s=180),
    Platform.facebook_reels: Preset(platform=Platform.facebook_reels, fmt=ExportFormat.vertical, width=1080,
                                    height=1920, fps_max=60, video_bitrate="10M", max_duration_s=90,
                                    min_duration_s=3, note="Reels publishing API: 3–90 s"),
    Platform.zalo: Preset(platform=Platform.zalo, fmt=ExportFormat.vertical, width=1080, height=1920, fps_max=30,
                          video_bitrate="6M", max_duration_s=None, note="limits depend on OA permissions"),
    Platform.youtube: Preset(platform=Platform.youtube, fmt=ExportFormat.horizontal, width=1920, height=1080,
                             fps_max=60, video_bitrate="12M", max_duration_s=None),
}

FORMAT_SIZE = {ExportFormat.vertical: (1080, 1920), ExportFormat.horizontal: (1920, 1080),
               ExportFormat.square: (1080, 1080), ExportFormat.portrait: (1080, 1350)}


class FormatInput(BaseModel):
    src_width: int
    src_height: int
    src_fps: float
    duration: float
    platform: Platform
    fit: Literal["blur_pad", "crop", "pad"] = "blur_pad"


class FormatOutput(BaseModel):
    preset: Preset
    width: int
    height: int
    fps: float
    graph: str          # [0:v] -> [vf]
    encode_args: list[str]
    upscaled: bool
    warnings: list[str]


def run(inp: FormatInput) -> FormatOutput:
    p = PRESETS[inp.platform]
    w, h = (p.width, p.height) if p.width else (inp.src_width, inp.src_height)
    fps = min(inp.src_fps or 30.0, p.fps_max)
    warnings = []
    same_aspect = abs(inp.src_width / inp.src_height - w / h) < 0.01
    if same_aspect:
        graph = f"[0:v]scale={w}:{h}:flags=lanczos,setsar=1[vf]"
    elif inp.fit == "crop":
        graph = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1[vf]"
    elif inp.fit == "pad":
        graph = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                 f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vf]")
    else:
        graph = (f"[0:v]split[fg][bg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                 f"gblur=sigma=40,eq=brightness=-0.08[bgb];[fg]scale={w}:{h}:force_original_aspect_ratio=decrease:"
                 f"flags=lanczos[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[vf]")
    # upscale if the visible picture needs more pixels than the source has
    scale_needed = max(w / inp.src_width, h / inp.src_height) if inp.fit == "crop" else min(
        w / inp.src_width, h / inp.src_height)
    upscaled = scale_needed > 1.01
    if upscaled:
        warnings.append(f"source {inp.src_width}x{inp.src_height} is upscaled to {w}x{h}; "
                        f"do not label as native {h}p")
    if p.max_duration_s and inp.duration > p.max_duration_s:
        warnings.append(f"{inp.duration:.1f}s exceeds {p.platform.value} max {p.max_duration_s:.0f}s")
    if p.min_duration_s and inp.duration < p.min_duration_s:
        warnings.append(f"{inp.duration:.1f}s below {p.platform.value} min {p.min_duration_s:.0f}s")
    enc = ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", f"{fps:g}",
           "-profile:v", "high", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    enc += ["-crf", "16"] if not p.video_bitrate else ["-b:v", p.video_bitrate, "-maxrate", p.video_bitrate,
                                                      "-bufsize", str(int(p.video_bitrate[:-1]) * 2) + "M"]
    return FormatOutput(preset=p, width=w, height=h, fps=fps, graph=graph, encode_args=enc,
                        upscaled=upscaled, warnings=warnings)


SPEC = SkillSpec("edit-multi-format", "format", "Platform-safe 9:16/16:9/1:1/4:5 export presets",
                 FormatInput, FormatOutput, run)
