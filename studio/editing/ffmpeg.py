"""Thin, testable FFmpeg/ffprobe wrapper used by every editing skill.

All media work that can be done deterministically goes through here instead of
an AI call (token budget rule). Commands are argv lists — never shell strings —
so paths with spaces or quotes are safe.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from studio.editing.schemas import MediaInfo

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = int(os.environ.get("STUDIO_FFMPEG_TIMEOUT", "1800"))


class FFmpegError(RuntimeError):
    def __init__(self, msg: str, stderr: str = ""):
        self.stderr = stderr[-2000:]
        tail = " | ".join(l for l in self.stderr.strip().splitlines()[-3:])
        super().__init__(f"{msg}: {tail}" if tail else msg)


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def run(args: list[str], timeout: int = DEFAULT_TIMEOUT, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", *args]
    logger.debug("ffmpeg %s", " ".join(args[:12]))
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(f"ffmpeg timed out after {timeout}s") from e
    if check and res.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({res.returncode})", res.stderr)
    return res


def run_bytes(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> bytes:
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", *args]
    res = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if res.returncode != 0:
        raise FFmpegError("ffmpeg (bytes) failed", res.stderr.decode(errors="replace"))
    return res.stdout


def _ratio(v: str | None) -> float:
    if not v or v in ("0/0", "N/A"):
        return 0.0
    if "/" in v:
        n, d = v.split("/", 1)
        return float(n) / float(d) if float(d) else 0.0
    return float(v)


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def probe(path: str | Path, with_checksum: bool = False) -> MediaInfo:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    res = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)],
        capture_output=True, text=True, timeout=60,
    )
    if res.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {p.name}", res.stderr)
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    dur = float(fmt.get("duration") or (v or {}).get("duration") or (a or {}).get("duration") or 0)
    return MediaInfo(
        path=str(p),
        duration=round(dur, 3),
        width=int(v["width"]) if v else 0,
        height=int(v["height"]) if v else 0,
        fps=round(_ratio((v or {}).get("avg_frame_rate") or (v or {}).get("r_frame_rate")), 3) if v else 0.0,
        video_codec=(v or {}).get("codec_name"),
        pix_fmt=(v or {}).get("pix_fmt"),
        has_audio=a is not None,
        audio_codec=(a or {}).get("codec_name"),
        sample_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else 0,
        size_bytes=int(fmt.get("size") or p.stat().st_size),
        checksum=sha256_file(p) if with_checksum else None,
    )


def parse_float_events(stderr: str, key: str) -> list[float]:
    return [float(m) for m in re.findall(rf"{key}[:=]\s*(-?\d+(?:\.\d+)?)", stderr)]


@lru_cache(maxsize=1)
def default_font() -> str | None:
    """A font with Vietnamese glyphs. STUDIO_FONT overrides."""
    env = os.environ.get("STUDIO_FONT")
    if env and Path(env).exists():
        return env
    if shutil.which("fc-match"):
        for fam in ("Be Vietnam Pro:bold", "Montserrat:bold", "Roboto:bold", "DejaVu Sans:bold"):
            r = subprocess.run(["fc-match", "-f", "%{file}", fam], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout and Path(r.stdout).exists():
                return r.stdout
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
        if Path(cand).exists():
            return cand
    return None


def esc_filter_path(path: str | Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    s = str(path).replace("\\", "/")
    return s.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")


def esc_text(text: str) -> str:
    """Escape text for drawtext (used with text='...')."""
    return (text.replace("\\", "\\\\").replace("'", "’").replace(":", r"\:")
            .replace("%", r"\%").replace(",", r"\,"))
