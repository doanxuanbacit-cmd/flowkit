"""End-to-end render of a 3-clip timeline through the whole editing engine."""
import json
from pathlib import Path

import pytest

from studio.editing import ffmpeg
from studio.editing.engine import Engine, RenderOptions, to_edl
from studio.editing.schemas import (
    AudioCue, Genre, Overlay, OverlayKind, Platform, SubtitleCue, Timeline, TimelineClip, Transition, TransitionType,
)


@pytest.fixture
def timeline(media):
    a = media.clip("a", dur=2.5, color="0x803030", freq=300)
    b = media.clip("b", dur=2.5, color="0x308030", freq=500, lead_silence=0.5)
    c = media.clip("c", dur=2.0, color="0x303080", freq=700)
    music = media.wav("music", "'0.3*sin(2*PI*110*t)'", dur=10)
    return Timeline(
        id="film1", title="Phim mẫu", genre=Genre.drama, width=540, height=960, fps=30,
        clips=[TimelineClip(id="s1", source=a, characters=["Lan"]),
               TimelineClip(id="s2", source=b, in_s=0.5, characters=["Lan"],
                            transition_in=Transition(type=TransitionType.j_cut, audio_offset=0.4)),
               TimelineClip(id="s3", source=c, transition_in=Transition(type=TransitionType.crossfade, duration=0.5))],
        music=[AudioCue(source=music, gain_db=-8, fade_out=1.0, duration=8)],
        subtitles=[SubtitleCue(start=0.2, end=2.0, text="Chào buổi sáng, Lan!"),
                   SubtitleCue(start=2.6, end=4.5, text="Hôm nay mình đi đâu?")],
        overlays=[Overlay(kind=OverlayKind.title, start=0, end=1.5, text="Phim mẫu"),
                  Overlay(kind=OverlayKind.cta, start=5, end=6.4, text="Theo dõi Xuân Bắc AI")],
        renditions=[Platform.tiktok, Platform.facebook_reels],
    )


def test_render_full_pipeline(timeline, tmp_path):
    stages = []
    eng = Engine(tmp_path / "cache", progress=lambda s, p: stages.append(s))
    res = eng.render(timeline, tmp_path / "out", RenderOptions(preset="ultrafast"))
    # 2.5 + 2.5 + 2.0 - 0.5 crossfade
    assert res.duration == pytest.approx(6.5, abs=0.05)
    m = ffmpeg.probe(res.master)
    assert (m.width, m.height) == (540, 960) and m.has_audio
    assert m.duration == pytest.approx(6.5, abs=0.1)
    for r in res.renditions:
        info = ffmpeg.probe(r.path)
        assert (info.width, info.height) == (1080, 1920)
        assert r.upscaled  # 540x960 source -> honest flag
    qa = json.loads(Path(res.qa_report).read_text())
    assert "master" in qa and "tiktok" in qa["renditions"]
    assert qa["master"]["metrics"]["lufs"] == pytest.approx(-16, abs=1.5)  # drama target
    man = json.loads(Path(res.manifest).read_text())
    assert "master.mp4" in man["outputs"] and set(man["inputs"]) == {"s1", "s2", "s3"}
    assert Path(res.srt).read_text(encoding="utf-8").count("-->") == 2
    assert Path(res.thumbnail).stat().st_size > 0
    assert stages[0] == "analyze" and stages[-1] == "done"
    assert res.cache_misses == 3

    # second render hits the cache; changing one clip re-renders only that clip
    eng2 = Engine(tmp_path / "cache")
    eng2.render(timeline, tmp_path / "out2", RenderOptions(preset="ultrafast", run_continuity=False))
    assert (eng2.hits, eng2.misses) == (3, 0)
    timeline.clips[2].out_s = 1.5
    eng3 = Engine(tmp_path / "cache")
    eng3.render(timeline, tmp_path / "out3", RenderOptions(preset="ultrafast", run_continuity=False))
    assert (eng3.hits, eng3.misses) == (2, 1)


def test_render_silent_clips_and_edl(media, tmp_path):
    a = media.clip("q1", dur=1.5, tone=False, size="320x320")
    b = media.clip("q2", dur=1.5, tone=False, size="320x320", color="blue")
    tl = Timeline(id="sq", width=320, height=320, clips=[TimelineClip(id="x", source=a), TimelineClip(id="y", source=b)],
                  renditions=[])
    res = Engine(tmp_path / "c").render(tl, tmp_path / "o", RenderOptions(preset="ultrafast", apply_director=False))
    assert ffmpeg.probe(res.master).has_audio
    assert any("silent" in n for n in res.notes)
    edl = to_edl(tl)
    assert "001  AX" in edl and "FROM CLIP NAME: q2.mp4" in edl


def test_out_of_range_rejected(media, tmp_path):
    a = media.clip("r", dur=1)
    tl = Timeline(id="r", clips=[TimelineClip(id="x", source=a, out_s=5)])
    with pytest.raises(ValueError):
        Engine(tmp_path / "c").render(tl, tmp_path / "o")
