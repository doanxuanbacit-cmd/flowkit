"""Professional Editing Engine — renders a Timeline (EDL) to deliverables.

Stage order is fixed and each stage is owned by exactly one skill slot, so no
effect is ever applied twice:

  style (director) → per-clip conform + colour (cached) → transitions/J-L audio →
  graphics → subtitles burn → audio stems + mastering (two-pass loudness) →
  master mux → platform renditions → thumbnail → QA → manifest

Per-clip intermediates are content-addressed, so fixing one clip re-renders
that clip only; the rest is reused from cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Callable

import numpy as np
from pydantic import BaseModel, Field

from studio.editing import ffmpeg, frames
from studio.editing.schemas import AudioCue, Platform, QAReport, Timeline, TransitionType
from studio.editing.skills import (
    edit_audio_master, edit_auto_subtitles, edit_color_grading, edit_continuity, edit_director,
    edit_master_qa, edit_motion_graphics, edit_multi_format, edit_transition_pro,
)

logger = logging.getLogger(__name__)
ENGINE_VERSION = "4.0.0"
Progress = Callable[[str, float], None]


class RenderOptions(BaseModel):
    apply_director: bool = True
    auto_match_color: bool = True
    run_continuity: bool = True
    fit: str = "blur_pad"
    crf: int = Field(16, ge=0, le=35)
    preset: str = "medium"
    force_clips: list[str] = Field(default_factory=list)   # re-render these clip intermediates


class Rendition(BaseModel):
    platform: Platform
    path: str
    width: int
    height: int
    upscaled: bool
    qa: QAReport
    warnings: list[str] = []


class RenderResult(BaseModel):
    timeline_id: str
    out_dir: str
    master: str
    renditions: list[Rendition]
    thumbnail: str
    srt: str | None
    ass: str | None
    metadata: str
    manifest: str
    qa_report: str
    qa_passed: bool
    duration: float
    cache_hits: int
    cache_misses: int
    notes: list[str] = []


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]


def _src_sig(path: str) -> list:
    st = Path(path).stat()
    return [str(Path(path).resolve()), st.st_size, int(st.st_mtime)]


def _conform_graph(src_w: int, src_h: int, w: int, h: int, fit: str) -> str:
    """[0:v] -> [cf] scaled/fitted to the timeline frame."""
    if abs(src_w / src_h - w / h) < 0.01:
        return f"[0:v]scale={w}:{h}:flags=lanczos,setsar=1[cf]"
    if fit == "crop":
        return f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1[cf]"
    if fit == "pad":
        return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[cf]")
    return (f"[0:v]split[fg][bg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"gblur=sigma=40,eq=brightness=-0.08[bgb];[fg]scale={w}:{h}:force_original_aspect_ratio=decrease:"
            f"flags=lanczos[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[cf]")


class Engine:
    def __init__(self, cache_dir: str | Path, progress: Progress | None = None):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.progress = progress or (lambda stage, pct: None)
        self.hits = 0
        self.misses = 0

    # ---------------------------------------------------------------- stage A
    def conform_clip(self, tl: Timeline, clip, info, opts: RenderOptions) -> Path:
        in_s = clip.in_s
        out_s = clip.out_s if clip.out_s is not None else info.duration
        key = _key("clip", ENGINE_VERSION, _src_sig(clip.source), in_s, out_s, clip.color.model_dump(),
                   tl.width, tl.height, tl.fps, opts.fit, opts.crf)
        out = self.cache / f"clip_{key}.mp4"
        if out.exists() and clip.id not in opts.force_clips:
            self.hits += 1
            return out
        self.misses += 1
        g = _conform_graph(info.width, info.height, tl.width, tl.height, opts.fit)
        g += ";" + edit_color_grading.color_graph(clip.color, "cf", "cg")
        g += f";[cg]fps={tl.fps:g},format=yuv420p[vo]"
        tmp = out.with_suffix(".part.mp4")
        ffmpeg.run(["-y", "-loglevel", "error", "-ss", f"{in_s:.3f}", "-t", f"{out_s - in_s:.3f}", "-i", clip.source,
                    "-filter_complex", g, "-map", "[vo]", "-an", "-c:v", "libx264", "-preset", opts.preset,
                    "-crf", str(opts.crf), "-pix_fmt", "yuv420p", str(tmp)])
        tmp.replace(out)
        return out

    def clip_audio(self, clip, info, head: float, tail: float) -> tuple[Path | None, float, float]:
        if clip.mute or not info.has_audio:
            return None, 0.0, 0.0
        out_s = clip.out_s if clip.out_s is not None else info.duration
        head = min(head, clip.in_s)
        tail = max(0.0, min(tail, info.duration - out_s))
        start = clip.in_s - head
        dur = (out_s - clip.in_s) + head + tail
        key = _key("aud", _src_sig(clip.source), start, dur, clip.volume_db)
        out = self.cache / f"aud_{key}.wav"
        if not out.exists():
            ffmpeg.run(["-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", clip.source,
                        "-vn", "-af", f"volume={clip.volume_db}dB", "-ac", "2", "-ar", "48000",
                        "-c:a", "pcm_s16le", str(out)])
        return out, head, tail

    # ---------------------------------------------------------------- audio stems
    @staticmethod
    def _cue_chain(i: int, cue: AudioCue, total: float) -> str:
        f = [f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo"]
        dur = cue.duration
        f.append(f"atrim=start={cue.in_s:.3f}" + (f":duration={dur:.3f}" if dur else "") + ",asetpts=PTS-STARTPTS")
        if cue.gain_db:
            f.append(f"volume={cue.gain_db}dB")
        if cue.fade_in:
            f.append(f"afade=t=in:d={cue.fade_in:.3f}")
        if cue.fade_out and dur:
            f.append(f"afade=t=out:st={max(0.0, dur - cue.fade_out):.3f}:d={cue.fade_out:.3f}")
        ms = int(cue.start_s * 1000)
        f.append(f"adelay={ms}|{ms},apad,atrim=duration={total:.3f}")
        return ",".join(f)

    def build_stem(self, cues: list[AudioCue], base: Path | None, total: float, name: str, out_dir: Path) -> Path | None:
        if not cues and base is None:
            return None
        inputs, parts, labels = [], [], []
        if base is not None:
            inputs += ["-i", str(base)]
            parts.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={total:.3f}[s0]")
            labels.append("[s0]")
        for cue in cues:
            if not Path(cue.source).exists():
                raise FileNotFoundError(cue.source)
            idx = len(labels)
            inputs += ["-i", cue.source]
            parts.append(self._cue_chain(idx, cue, total) + f"[s{idx}]")
            labels.append(f"[s{idx}]")
        if len(labels) == 1:
            parts.append(f"{labels[0]}anull[st]")
        else:
            parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest,"
                         f"atrim=duration={total:.3f}[st]")
        out = out_dir / f"stem_{name}.wav"
        ffmpeg.run(["-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts), "-map", "[st]",
                    "-c:a", "pcm_s16le", str(out)])
        return out

    # ---------------------------------------------------------------- main
    def render(self, timeline: Timeline, out_dir: str | Path, opts: RenderOptions | None = None) -> RenderResult:
        opts = opts or RenderOptions()
        t0 = time.time()
        out = Path(out_dir)
        work = out / "work"
        work.mkdir(parents=True, exist_ok=True)
        notes: list[str] = []
        tl = timeline.model_copy(deep=True)
        self.progress("analyze", 0.02)

        if opts.apply_director:
            d = edit_director.run(edit_director.DirectorInput(timeline=tl))
            tl = d.timeline
            notes += d.notes
        infos = {}
        for c in tl.clips:
            if not Path(c.source).exists():
                raise FileNotFoundError(f"clip {c.id}: {c.source}")
            infos[c.id] = ffmpeg.probe(c.source)
            if not infos[c.id].width:
                raise ValueError(f"clip {c.id} has no video stream")
            if c.out_s is not None and c.out_s > infos[c.id].duration + 0.05:
                raise ValueError(f"clip {c.id}: out_s {c.out_s} beyond source duration {infos[c.id].duration}")
        durations = {k: v.duration for k, v in infos.items()}
        small = [c.id for c in tl.clips
                 if min(tl.width / infos[c.id].width, tl.height / infos[c.id].height) > 1.01]
        if small:
            notes.append(f"clips {', '.join(small)} are smaller than the {tl.width}x{tl.height} frame and are "
                         f"upscaled; output is not native {tl.height}p")

        if opts.auto_match_color and len(tl.clips) > 1:
            grades = edit_color_grading.run(edit_color_grading.ColorInput(clips=tl.clips, durations=durations))
            gmap = {g.clip_id: g.color for g in grades.grades}
            for c in tl.clips:
                c.color = gmap[c.id]

        # ---- stage A: per-clip conform (cached)
        vids, auds, joins = [], [], []
        for i, c in enumerate(tl.clips):
            self.progress("conform", 0.05 + 0.35 * i / len(tl.clips))
            info = infos[c.id]
            vids.append(self.conform_clip(tl, c, info, opts))
            nxt = tl.clips[i + 1].transition_in if i + 1 < len(tl.clips) else None
            want_head = c.transition_in.audio_offset if c.transition_in.type == TransitionType.j_cut else 0.0
            want_tail = nxt.audio_offset if nxt and nxt.type == TransitionType.l_cut else 0.0
            a, head, tail = self.clip_audio(c, info, want_head, want_tail) if tl.keep_clip_audio else (None, 0, 0)
            if want_head and head < want_head:
                notes.append(f"{c.id}: J-cut lead limited to {head:.2f}s (no handle before in-point)")
            if want_tail and tail < want_tail:
                notes.append(f"{c.id}: L-cut trail limited to {tail:.2f}s (no handle after out-point)")
            auds.append(a)
            out_s = c.out_s if c.out_s is not None else info.duration
            joins.append(dict(duration=out_s - c.in_s, head=head, tail=tail, tr=c.transition_in))

        # continuity is judged on the graded, conformed shots — what the viewer sees
        continuity = None
        if opts.run_continuity and len(tl.clips) > 1:
            graded = [c.model_copy(update={"source": str(vids[i]), "in_s": 0.0, "out_s": None})
                      for i, c in enumerate(tl.clips)]
            continuity = edit_continuity.run(edit_continuity.ContinuityInput(
                clips=graded, durations={c.id: joins[i]["duration"] for i, c in enumerate(tl.clips)},
                contact_sheet=str(out / "continuity_sheet.jpg")))

        # ---- stage B: joins
        self.progress("assemble", 0.45)
        inputs, jc = [], []
        for v in vids:
            inputs += ["-i", str(v)]
        a_idx = len(vids)
        for i, a in enumerate(auds):
            ai = None
            if a is not None:
                inputs += ["-i", str(a)]
                ai = a_idx
                a_idx += 1
            jc.append(edit_transition_pro.JoinClip(video_input=i, audio_input=ai, duration=joins[i]["duration"],
                                                   audio_head=joins[i]["head"], audio_tail=joins[i]["tail"],
                                                   transition_in=joins[i]["tr"]))
        tr = edit_transition_pro.run(edit_transition_pro.TransitionInput(clips=jc, fps=tl.fps))
        notes += tr.warnings
        total = tr.total_duration

        # ---- stage C: graphics + subtitles on the assembled picture
        subs = None
        if tl.subtitles:
            subs = edit_auto_subtitles.run(edit_auto_subtitles.SubtitleInput(
                out_dir=str(out), basename="subtitles", width=tl.width, height=tl.height, duration=total,
                style=tl.subtitle_style, cues=tl.subtitles))
            notes += subs.warnings
        gfx = edit_motion_graphics.run(edit_motion_graphics.GraphicsInput(
            overlays=tl.overlays, width=tl.width, height=tl.height, first_extra_input=a_idx,
            src=tr.video_label, dst="gfx"))
        notes += gfx.warnings
        for img in gfx.extra_inputs:
            inputs += ["-loop", "1", "-i", img]
        graph = tr.filter_complex + ";" + gfx.graph
        vlast = "gfx"
        if subs and tl.subtitle_style.burn_in and subs.cues:
            graph += f";[gfx]{edit_auto_subtitles.burn_filter(subs.ass)}[vsub]"
            vlast = "vsub"
        picture = work / "picture.mp4"
        clipaudio = work / "clip_audio.wav" if tr.audio_label else None
        cmd = ["-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", f"[{vlast}]",
               "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf), "-pix_fmt", "yuv420p",
               "-r", f"{tl.fps:g}", "-t", f"{total:.3f}", str(picture)]
        if clipaudio:
            cmd += ["-map", f"[{tr.audio_label}]", "-c:a", "pcm_s16le", "-t", f"{total:.3f}", str(clipaudio)]
        self.progress("picture", 0.55)
        ffmpeg.run(cmd)

        # ---- audio
        self.progress("audio", 0.7)
        dlg = self.build_stem(tl.voice, clipaudio, total, "dialogue", work)
        mus = self.build_stem(tl.music, None, total, "music", work)
        sfx = self.build_stem(tl.sfx, None, total, "sfx", work)
        premaster = None
        if dlg or mus or sfx:
            ins, labs = [], {}
            for name, p in (("d", dlg), ("m", mus), ("s", sfx)):
                if p:
                    labs[name] = f"{len(ins) // 2}:a"
                    ins += ["-i", str(p)]
            mg = edit_audio_master.mix_graph(tl.audio, labs.get("d"), labs.get("m"), labs.get("s"), "mix")
            premaster = work / "premaster.wav"
            ffmpeg.run(["-y", "-loglevel", "error", *ins, "-filter_complex", mg, "-map", "[mix]",
                        "-t", f"{total:.3f}", "-c:a", "pcm_s16le", str(premaster)])
        master = out / "master.mp4"
        if premaster:
            am = edit_audio_master.run(edit_audio_master.AudioMasterInput(premaster=str(premaster), spec=tl.audio))
            ffmpeg.run(["-y", "-loglevel", "error", "-i", str(picture), "-i", str(premaster),
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", am.loudnorm_filter,
                        "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-t", f"{total:.3f}",
                        "-movflags", "+faststart", str(master)])
            if am.silent:
                notes.append("audio is silent; loudness normalisation skipped")
        else:
            ffmpeg.run(["-y", "-loglevel", "error", "-i", str(picture), "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                        "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(master)])
            notes.append("no audio in timeline; silent track added")

        clip_map = [edit_master_qa.ClipSpan(clip_id=c.id, start=tr.starts[i],
                                            end=tr.starts[i + 1] if i + 1 < len(tl.clips) else total)
                    for i, c in enumerate(tl.clips)]
        has_audio_content = bool(premaster)
        qa_target = tl.audio.target_lufs if has_audio_content else None

        # ---- renditions
        rends: list[Rendition] = []
        minfo = ffmpeg.probe(master)
        for k, plat in enumerate(tl.renditions):
            self.progress("renditions", 0.78 + 0.12 * k / max(1, len(tl.renditions)))
            fo = edit_multi_format.run(edit_multi_format.FormatInput(
                src_width=minfo.width, src_height=minfo.height, src_fps=minfo.fps, duration=total,
                platform=plat, fit=opts.fit))
            rp = out / f"{plat.value}.mp4"
            ffmpeg.run(["-y", "-loglevel", "error", "-i", str(master), "-filter_complex", fo.graph,
                        "-map", "[vf]", "-map", "0:a", *fo.encode_args, str(rp)])
            qa = edit_master_qa.run(edit_master_qa.QAInput(
                path=str(rp), expect_width=fo.width, expect_height=fo.height, expect_fps=fo.fps,
                target_lufs=qa_target, subtitles=subs.cues if subs else [], subtitle_style=tl.subtitle_style,
                clip_map=clip_map))
            rends.append(Rendition(platform=plat, path=str(rp), width=fo.width, height=fo.height,
                                   upscaled=fo.upscaled or bool(small), qa=qa, warnings=fo.warnings))
        master_qa = edit_master_qa.run(edit_master_qa.QAInput(
            path=str(master), expect_width=tl.width, expect_height=tl.height, expect_fps=tl.fps,
            target_lufs=qa_target, subtitles=subs.cues if subs else [], subtitle_style=tl.subtitle_style,
            clip_map=clip_map))

        # ---- thumbnail: highest-contrast frame among a few candidates
        self.progress("thumbnail", 0.93)
        best_t, best_v = total * 0.3, -1.0
        for frac in (0.15, 0.3, 0.45, 0.6, 0.75):
            img = frames.grab_rgb(str(master), total * frac, 96, 96)
            v = float(np.std(img))
            if v > best_v:
                best_t, best_v = total * frac, v
        thumb = out / "thumbnail.jpg"
        ffmpeg.run(["-y", "-loglevel", "error", "-ss", f"{best_t:.3f}", "-i", str(master), "-frames:v", "1",
                    "-q:v", "2", str(thumb)])

        # ---- reports
        cont_issues = continuity.issues if continuity else []
        all_passed = master_qa.passed and all(r.qa.passed for r in rends)
        qa_doc = {"passed": all_passed, "master": master_qa.model_dump(),
                  "renditions": {r.platform.value: r.qa.model_dump() for r in rends},
                  "continuity": [i.model_dump() for i in cont_issues],
                  "continuity_joins": [j.model_dump() for j in continuity.joins] if continuity else [],
                  "contact_sheet": continuity.contact_sheet if continuity else None}
        qa_path = out / "qa_report.json"
        qa_path.write_text(json.dumps(qa_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        meta = {"timeline_id": tl.id, "title": tl.title, "genre": tl.genre.value, "duration": total,
                "width": tl.width, "height": tl.height, "fps": tl.fps,
                "clips": [{"id": c.id, "start": clip_map[i].start, "end": clip_map[i].end, "source": c.source,
                           "in": c.in_s, "out": c.out_s, "transition_in": c.transition_in.type.value,
                           "color": c.color.model_dump()} for i, c in enumerate(tl.clips)],
                "renditions": [{"platform": r.platform.value, "width": r.width, "height": r.height,
                                "upscaled": r.upscaled} for r in rends],
                "upscaled_clips": small,
                "loudness_target": tl.audio.target_lufs, "notes": notes}
        meta_path = out / "metadata.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        files = [master, thumb, qa_path, meta_path] + [Path(r.path) for r in rends]
        if subs:
            files += [Path(subs.srt), Path(subs.ass)]
        manifest = {"engine": ENGINE_VERSION, "timeline": tl.model_dump(mode="json"),
                    "inputs": {c.id: ffmpeg.sha256_file(c.source) for c in tl.clips},
                    "outputs": {p.name: {"sha256": ffmpeg.sha256_file(p), "bytes": p.stat().st_size} for p in files},
                    "render_seconds": round(time.time() - t0, 2),
                    "cache": {"hits": self.hits, "misses": self.misses}}
        man_path = out / "manifest.json"
        man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
        self.progress("done", 1.0)
        return RenderResult(timeline_id=tl.id, out_dir=str(out), master=str(master), renditions=rends,
                            thumbnail=str(thumb), srt=subs.srt if subs else None, ass=subs.ass if subs else None,
                            metadata=str(meta_path), manifest=str(man_path), qa_report=str(qa_path),
                            qa_passed=all_passed, duration=total, cache_hits=self.hits,
                            cache_misses=self.misses, notes=notes)


def to_edl(tl: Timeline, starts: list[float] | None = None) -> str:
    """CMX3600-style EDL text for interchange with NLEs (Premiere/Resolve)."""
    def tc(t: float) -> str:
        f = int(round(t * tl.fps))
        fps = int(round(tl.fps))
        return f"{f // (3600 * fps):02d}:{f // (60 * fps) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"
    lines = [f"TITLE: {tl.title or tl.id}", "FCM: NON-DROP FRAME", ""]
    rec = 0.0
    for i, c in enumerate(tl.clips, 1):
        out_s = c.out_s if c.out_s is not None else c.in_s
        dur = out_s - c.in_s
        tr = c.transition_in
        kind, frames_ = ("D", int(round(tr.duration * tl.fps))) if tr.is_overlap else ("C", 0)
        if starts:
            rec = starts[i - 1]
        lines.append(f"{i:03d}  AX       AA/V  {kind}{(' %03d' % frames_) if kind == 'D' else '    '} "
                     f"{tc(c.in_s)} {tc(out_s)} {tc(rec)} {tc(rec + dur)}")
        lines.append(f"* FROM CLIP NAME: {Path(c.source).name}")
        if not starts:
            rec += dur - (tr.duration if tr.is_overlap else 0)
    return "\n".join(lines) + "\n"
