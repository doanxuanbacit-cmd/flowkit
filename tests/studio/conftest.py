"""Synthetic media fixtures (generated with FFmpeg lavfi — no binary files in git)."""
import subprocess
from pathlib import Path

import pytest


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, timeout=120)


def make_clip(path: Path, dur=2.0, color="red", size="270x480", freq=440, rate=30, tone=True, lead_silence=0.0,
              tail_silence=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    total = lead_silence + dur + tail_silence
    vsrc = f"color=c={color}:s={size}:d={total}:r={rate}"
    if tone:
        a = (f"sine=frequency={freq}:duration={dur}:sample_rate=48000,adelay={int(lead_silence*1000)}|{int(lead_silence*1000)},"
             f"apad=whole_dur={total}")
        ff("-f", "lavfi", "-i", vsrc, "-f", "lavfi", "-i", a, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-t", str(total), str(path))
    else:
        ff("-f", "lavfi", "-i", vsrc, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(total), str(path))
    return path


@pytest.fixture
def media(tmp_path):
    class M:
        root = tmp_path
        def clip(self, name, **kw):
            return str(make_clip(tmp_path / f"{name}.mp4", **kw))
        def wav(self, name, expr, dur=4.0):
            p = tmp_path / f"{name}.wav"
            ff("-f", "lavfi", "-i", f"aevalsrc={expr}:s=22050:d={dur}", "-ac", "1", str(p))
            return str(p)
    return M()
