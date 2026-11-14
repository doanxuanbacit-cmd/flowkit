"""edit-director — genre-driven editorial style.

Deterministic: maps a genre to pacing, a transition palette and a cap on how
many joins may be non-cuts ("storytelling over effects"), then assigns
transitions to a timeline that has none yet. Joins the editor already set are
never overwritten.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from studio.editing.schemas import Genre, Timeline, Transition, TransitionType
from studio.editing.skills.base import SkillSpec


class StylePreset(BaseModel):
    genre: Genre
    avg_shot_s: float
    default_transition: TransitionType
    transition_duration: float
    max_transition_ratio: float = Field(ge=0, le=1)  # share of joins that may be non-cut
    color_look: str
    target_lufs: float
    music_under_voice_db: float
    subtitle_font_size: int


PRESETS: dict[Genre, StylePreset] = {
    Genre.short_film: StylePreset(genre=Genre.short_film, avg_shot_s=4.0, default_transition=TransitionType.crossfade,
                                  transition_duration=0.5, max_transition_ratio=0.25, color_look="filmic",
                                  target_lufs=-14, music_under_voice_db=-14, subtitle_font_size=60),
    Genre.drama: StylePreset(genre=Genre.drama, avg_shot_s=5.0, default_transition=TransitionType.j_cut,
                             transition_duration=0.0, max_transition_ratio=0.35, color_look="filmic",
                             target_lufs=-16, music_under_voice_db=-16, subtitle_font_size=58),
    Genre.tvc: StylePreset(genre=Genre.tvc, avg_shot_s=1.8, default_transition=TransitionType.cut,
                           transition_duration=0.0, max_transition_ratio=0.15, color_look="punchy",
                           target_lufs=-14, music_under_voice_db=-10, subtitle_font_size=66),
    Genre.ugc: StylePreset(genre=Genre.ugc, avg_shot_s=2.5, default_transition=TransitionType.cut,
                           transition_duration=0.0, max_transition_ratio=0.1, color_look="natural",
                           target_lufs=-14, music_under_voice_db=-18, subtitle_font_size=70),
    Genre.podcast: StylePreset(genre=Genre.podcast, avg_shot_s=6.0, default_transition=TransitionType.l_cut,
                               transition_duration=0.0, max_transition_ratio=0.3, color_look="natural",
                               target_lufs=-16, music_under_voice_db=-22, subtitle_font_size=62),
    Genre.comedy: StylePreset(genre=Genre.comedy, avg_shot_s=2.2, default_transition=TransitionType.cut,
                              transition_duration=0.0, max_transition_ratio=0.1, color_look="bright",
                              target_lufs=-14, music_under_voice_db=-14, subtitle_font_size=70),
    Genre.action: StylePreset(genre=Genre.action, avg_shot_s=1.5, default_transition=TransitionType.whip_pan,
                              transition_duration=0.25, max_transition_ratio=0.2, color_look="teal_orange",
                              target_lufs=-12, music_under_voice_db=-8, subtitle_font_size=66),
}

LOOKS = {  # gentle, never destructive
    "natural": dict(contrast=1.0, saturation=1.0),
    "filmic": dict(contrast=1.06, saturation=0.92, gamma=0.98),
    "punchy": dict(contrast=1.1, saturation=1.15),
    "bright": dict(brightness=0.03, saturation=1.08),
    "teal_orange": dict(contrast=1.08, saturation=1.05, r=0.04, b=-0.03),
}


class DirectorInput(BaseModel):
    timeline: Timeline
    override_existing: bool = False


class DirectorOutput(BaseModel):
    timeline: Timeline
    preset: StylePreset
    assigned: dict[str, str]  # clip_id -> transition type
    notes: list[str]


def run(inp: DirectorInput) -> DirectorOutput:
    tl = inp.timeline.model_copy(deep=True)
    preset = PRESETS[tl.genre]
    joins = tl.clips[1:]
    budget = int(len(joins) * preset.max_transition_ratio + 1e-9)
    already = sum(1 for c in joins if c.transition_in.type != TransitionType.cut)
    assigned: dict[str, str] = {}
    notes: list[str] = []
    if preset.default_transition == TransitionType.cut:
        notes.append("genre favours hard cuts; no transitions added")
    # Space transitions evenly: prefer the act breaks rather than every join.
    if budget > already and preset.default_transition != TransitionType.cut:
        step = max(1, len(joins) // max(1, budget - already))
        for i in range(step - 1, len(joins), step):
            c = joins[i]
            if c.transition_in.type != TransitionType.cut and not inp.override_existing:
                continue
            if sum(1 for x in joins if x.transition_in.type != TransitionType.cut) >= budget:
                break
            t = preset.default_transition
            c.transition_in = Transition(
                type=t,
                duration=preset.transition_duration if t in (TransitionType.crossfade, TransitionType.whip_pan,
                                                              TransitionType.fade_black) else 0.0,
                audio_offset=0.4 if t in (TransitionType.j_cut, TransitionType.l_cut) else 0.0,
            )
            assigned[c.id] = t.value
    used = sum(1 for x in joins if x.transition_in.type != TransitionType.cut)
    if joins and used / len(joins) > preset.max_transition_ratio + 1e-9:
        notes.append(f"{used}/{len(joins)} joins are transitions — above the {preset.genre.value} cap; consider cuts")
    look = LOOKS[preset.color_look]
    for c in tl.clips:
        if c.color.is_identity():
            c.color = c.color.model_copy(update=look)
    tl.audio.target_lufs = preset.target_lufs
    tl.audio.duck_music_db = preset.music_under_voice_db
    tl.subtitle_style.font_size = preset.subtitle_font_size
    return DirectorOutput(timeline=tl, preset=preset, assigned=assigned, notes=notes)


SPEC = SkillSpec("edit-director", "style", "Genre-based pacing, transition budget, look and loudness target",
                 DirectorInput, DirectorOutput, run)
