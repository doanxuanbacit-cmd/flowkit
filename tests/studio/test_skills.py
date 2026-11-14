"""Unit tests for the 12 editing skills (real FFmpeg on synthetic media)."""
import json
import subprocess
from pathlib import Path

import pytest

from studio.editing import ffmpeg
from studio.editing.schemas import (
    AudioMasterSpec, ColorAdjust, Genre, Overlay, OverlayKind, Platform, SubtitleCue, SubtitleStyle, Timeline,
    TimelineClip, Transition, TransitionType,
)
from studio.editing.skills import (
    ALL, REGISTRY, SlotConflict, check_no_conflict, edit_audio_master, edit_auto_subtitles, edit_beat_sync,
    edit_color_grading, edit_continuity, edit_dialogue_sync, edit_director, edit_master_qa, edit_motion_graphics,
    edit_multi_format, edit_smart_cut, edit_transition_pro,
)


def test_registry_has_12_unique_slots():
    assert len(ALL) == 12 and len({s.slot for s in ALL}) == 12
    for s in ALL:
        sch = s.schema()
        assert sch["input"]["type"] == "object" and sch["output"]["type"] == "object"


def test_slot_conflict_detected():
    with pytest.raises(SlotConflict):
        check_no_conflict([REGISTRY["edit-color-grading"], REGISTRY["edit-color-grading"]])


def _tl(n=5, genre=Genre.short_film):
    return Timeline(id="t", genre=genre, clips=[TimelineClip(id=f"c{i}", source=f"/x/{i}.mp4") for i in range(n)])


def test_director_respects_transition_budget():
    out = edit_director.run(edit_director.DirectorInput(timeline=_tl(9)))
    used = [c for c in out.timeline.clips[1:] if c.transition_in.type != TransitionType.cut]
    assert len(used) <= int(8 * out.preset.max_transition_ratio)
    assert out.timeline.audio.target_lufs == out.preset.target_lufs
    assert out.timeline.clips[0].transition_in.type == TransitionType.cut


def test_director_keeps_editor_choice_and_cuts_for_ugc():
    tl = _tl(4, Genre.ugc)
    tl.clips[2].transition_in = Transition(type=TransitionType.crossfade, duration=0.4)
    out = edit_director.run(edit_director.DirectorInput(timeline=tl))
    assert out.timeline.clips[2].transition_in.type == TransitionType.crossfade
    assert not out.assigned


def test_smart_cut_trims_silence_and_finds_scene(media):
    a = media.clip("a", dur=1.5, color="red", lead_silence=1.0, tail_silence=1.0)
    out = edit_smart_cut.run(edit_smart_cut.SmartCutInput(path=a))
    assert 0.7 < out.trim_in < 1.0 and 2.5 < out.trim_out < 2.8
    # scene change: concat red + blue
    b = media.clip("b", dur=1.0, color="blue")
    cat = media.root / "cat.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", a, "-i", b, "-filter_complex",
                    "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", str(cat)], check=True)
    out2 = edit_smart_cut.run(edit_smart_cut.SmartCutInput(path=str(cat)))
    assert any(abs(t - 3.5) < 0.1 for t in out2.scene_changes)


def test_transition_graph_lengths():
    clips = [edit_transition_pro.JoinClip(video_input=0, audio_input=3, duration=3),
             edit_transition_pro.JoinClip(video_input=1, audio_input=4, duration=3,
                                          transition_in=Transition(type=TransitionType.crossfade, duration=0.5)),
             edit_transition_pro.JoinClip(video_input=2, audio_input=None, duration=2,
                                          transition_in=Transition(type=TransitionType.cut))]
    out = edit_transition_pro.run(edit_transition_pro.TransitionInput(clips=clips))
    assert out.total_duration == pytest.approx(7.5)
    assert out.starts == [0.0, 2.5, 5.5]
    assert "xfade=transition=fade" in out.filter_complex and "amix=inputs=2" in out.filter_complex


def test_continuity_flags_exposure_jump(media, tmp_path):
    a = media.clip("w", color="white", dur=1)
    b = media.clip("k", color="0x202020", dur=1)
    clips = [TimelineClip(id="a", source=a, characters=["Lan"]), TimelineClip(id="b", source=b, characters=["Lan"])]
    out = edit_continuity.run(edit_continuity.ContinuityInput(
        clips=clips, durations={"a": 1.0, "b": 1.0}, contact_sheet=str(tmp_path / "sheet.jpg")))
    codes = {i.code for i in out.issues}
    assert "CONT_EXPOSURE_JUMP" in codes and out.joins[0].same_scene
    assert Path(out.contact_sheet).stat().st_size > 0


def test_color_matching_moves_toward_reference(media):
    a = media.clip("g1", color="0x808080", dur=1)
    b = media.clip("g2", color="0x505050", dur=1)
    c = media.clip("g3", color="0x707070", dur=1)
    clips = [TimelineClip(id=k, source=p) for k, p in (("a", a), ("b", b), ("c", c))]
    out = edit_color_grading.run(edit_color_grading.ColorInput(clips=clips, durations={"a": 1, "b": 1, "c": 1}))
    assert out.reference == "c"
    g = {x.clip_id: x.color for x in out.grades}
    assert g["b"].brightness > 0 and g["a"].brightness < 0
    assert abs(g["a"].brightness) <= edit_color_grading.MAX_BRIGHT


def test_color_graph_and_lut_validation(tmp_path):
    assert edit_color_grading.color_graph(ColorAdjust(), "a", "b") == "[a]null[b]"
    lut = tmp_path / "x.cube"
    lut.write_text("LUT_3D_SIZE 2\n" + "0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n")
    g = edit_color_grading.color_graph(ColorAdjust(lut_path=str(lut), lut_strength=0.5), "a", "b")
    assert "lut3d" in g and "blend" in g
    bad = tmp_path / "bad.cube"
    bad.write_text("nope")
    with pytest.raises(ValueError):
        edit_color_grading.color_graph(ColorAdjust(lut_path=str(bad)), "a", "b")


def test_audio_master_measures_and_builds_second_pass(media):
    a = media.clip("tone", dur=3)
    out = edit_audio_master.run(edit_audio_master.AudioMasterInput(premaster=a))
    assert "measured_I=" in out.loudnorm_filter and not out.silent
    g = edit_audio_master.mix_graph(AudioMasterSpec(), "0:a", "1:a", None, "mix")
    assert "sidechaincompress" in g and "afftdn" in g and g.endswith("[mix]")


def test_dialogue_sync_detects_offset(media):
    ref = media.wav("ref", "'if(between(t,1,1.4)+between(t,2,2.2)+between(t,3,3.5),sin(2*PI*300*t),0)'")
    late = media.wav("late", "'if(between(t,1.2,1.6)+between(t,2.2,2.4)+between(t,3.2,3.7),sin(2*PI*300*t),0)'")
    out = edit_dialogue_sync.run(edit_dialogue_sync.SyncInput(clip_audio=late, reference=ref))
    assert out.offset_ms == pytest.approx(200, abs=20) and not out.in_sync
    assert out.fix_filter.startswith("adelay=")


def test_subtitles_script_alignment_and_formats(media, tmp_path):
    a = media.clip("talk", dur=2, lead_silence=1, tail_silence=1)
    out = edit_auto_subtitles.run(edit_auto_subtitles.SubtitleInput(
        out_dir=str(tmp_path), duration=4, audio=a,
        script_lines=["Xin chào các bạn", "Hôm nay mình sẽ chia sẻ một bí quyết rất hay về video AI"]))
    assert out.source == "script_aligned"
    assert out.cues[0].start >= 0.8 and out.cues[-1].end <= 3.3
    srt = Path(out.srt).read_text(encoding="utf-8")
    assert "Xin chào" in srt and "-->" in srt
    ass = Path(out.ass).read_text(encoding="utf-8")
    assert "PlayResY: 1920" in ass and ",345,1" in ass  # MarginV = 18% of 1920


def test_subtitles_asr_missing_is_reported(tmp_path, media):
    a = media.clip("x", dur=1)
    out = edit_auto_subtitles.run(edit_auto_subtitles.SubtitleInput(out_dir=str(tmp_path), duration=1, audio=a,
                                                                    use_asr=True))
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        assert out.cues == [] and "faster-whisper" in out.warnings[0]


def test_subtitle_segment_splits_long_cue():
    st = SubtitleStyle(max_chars_per_line=10, max_lines=1)
    cues = edit_auto_subtitles.segment([SubtitleCue(start=0, end=4, text="một hai ba bốn năm sáu bảy")], st)
    assert len(cues) >= 2 and cues[-1].end == pytest.approx(4)


def test_motion_graphics_graph_renders(media, tmp_path):
    src = media.clip("bg", dur=2, size="540x960")
    logo = tmp_path / "logo.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=yellow:s=200x80",
                    "-frames:v", "1", str(logo)], check=True)
    ovs = [Overlay(kind=OverlayKind.title, start=0, end=1.5, text="Xuân Bắc AI"),
           Overlay(kind=OverlayKind.lower_third, start=0.2, end=1.8, text="Anh Bắc", subtext="CEO"),
           Overlay(kind=OverlayKind.cta, start=1, end=2, text="Theo dõi ngay"),
           Overlay(kind=OverlayKind.kinetic, start=0.5, end=1.5, text="wow"),
           Overlay(kind=OverlayKind.logo, start=0, end=2, image=str(logo), position="top_right")]
    g = edit_motion_graphics.run(edit_motion_graphics.GraphicsInput(overlays=ovs, width=540, height=960, src="0:v",
                                                                    dst="out"))
    out = tmp_path / "g.mp4"
    ffmpeg.run(["-y", "-loglevel", "error", "-i", src, "-loop", "1", "-i", str(logo), "-filter_complex", g.graph,
                "-map", "[out]", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])
    assert ffmpeg.probe(out).duration == pytest.approx(2, abs=0.1)


def test_beat_sync_tempo_and_snap(media):
    clicks = media.wav("click", "'if(lt(mod(t,0.5),0.03),sin(2*PI*1000*t),0)'", dur=8)
    out = edit_beat_sync.run(edit_beat_sync.BeatInput(music=clicks, cut_points=[1.1, 2.9, 4.2]))
    assert out.bpm == pytest.approx(120, abs=3)
    assert out.moved >= 2
    assert min(abs(out.snapped[0] - b) for b in out.beats) < 0.02


def test_multi_format_flags_upscale_and_duration():
    o = edit_multi_format.run(edit_multi_format.FormatInput(src_width=720, src_height=1280, src_fps=24, duration=120,
                                                            platform=Platform.facebook_reels))
    assert o.upscaled and any("exceeds" in w for w in o.warnings)
    o2 = edit_multi_format.run(edit_multi_format.FormatInput(src_width=1080, src_height=1920, src_fps=30, duration=30,
                                                             platform=Platform.youtube))
    assert o2.width == 1920 and "gblur" in o2.graph


def test_master_qa_detects_black_and_clipping(media, tmp_path):
    p = tmp_path / "bad.mp4"
    ffmpeg.run(["-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1:r=30",
                "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1:r=30", "-f", "lavfi", "-i",
                "aevalsrc='2*gt(sin(2*PI*200*t),0)-1':s=48000:d=2", "-filter_complex",
                "[1:v][0:v]concat=n=2:v=1[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt",
                "yuv420p", "-c:a", "pcm_s16le", str(p.with_suffix(".mov"))])
    rep = edit_master_qa.run(edit_master_qa.QAInput(path=str(p.with_suffix(".mov")), expect_width=320,
                                                    expect_height=240, expect_fps=30))
    codes = {i.code for i in rep.issues}
    assert "BLACK_FRAMES" in codes and "AUDIO_CLIPPING" in codes and not rep.passed


def test_master_qa_subtitle_overflow():
    st = SubtitleStyle(font_size=80)
    iss = edit_master_qa.subtitle_issues([SubtitleCue(start=0, end=1, text="x" * 40),
                                          SubtitleCue(start=0.5, end=0.8, text="ok")], st, 1080, 1.0)
    codes = {i.code for i in iss}
    assert {"SUB_OVERFLOW", "SUB_OVERLAP", "SUB_TOO_FAST"} <= codes
