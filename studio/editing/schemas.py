"""Typed contracts (JSON-schema-able) shared by the editing skills and agents.

Every agent hand-off in Studio V4 is one of these models serialised to JSON, so
an AI provider can be swapped without touching the media code.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class MediaInfo(BaseModel):
    path: str
    duration: float
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: Optional[str] = None
    pix_fmt: Optional[str] = None
    has_audio: bool = False
    audio_codec: Optional[str] = None
    sample_rate: int = 0
    size_bytes: int = 0
    checksum: Optional[str] = None


class Genre(str, Enum):
    short_film = "short_film"
    tvc = "tvc"
    ugc = "ugc"
    podcast = "podcast"
    comedy = "comedy"
    drama = "drama"
    action = "action"


class TransitionType(str, Enum):
    cut = "cut"
    match_cut = "match_cut"      # hard cut, flagged for editorial intent
    crossfade = "crossfade"
    fade_black = "fade_black"
    whip_pan = "whip_pan"
    j_cut = "j_cut"              # incoming audio leads the picture cut
    l_cut = "l_cut"              # outgoing audio trails over the picture cut


class Transition(BaseModel):
    type: TransitionType = TransitionType.cut
    duration: float = Field(0.0, ge=0.0, le=3.0)   # picture overlap for dissolves
    audio_offset: float = Field(0.0, ge=0.0, le=3.0)  # J/L lead or trail

    @property
    def is_overlap(self) -> bool:
        return self.type in (TransitionType.crossfade, TransitionType.fade_black,
                             TransitionType.whip_pan) and self.duration > 0


class ColorAdjust(BaseModel):
    brightness: float = Field(0.0, ge=-1.0, le=1.0)
    contrast: float = Field(1.0, ge=0.0, le=3.0)
    saturation: float = Field(1.0, ge=0.0, le=3.0)
    gamma: float = Field(1.0, ge=0.1, le=4.0)
    # colorbalance midtones, -1..1
    r: float = Field(0.0, ge=-1.0, le=1.0)
    g: float = Field(0.0, ge=-1.0, le=1.0)
    b: float = Field(0.0, ge=-1.0, le=1.0)
    lut_path: Optional[str] = None
    lut_strength: float = Field(1.0, ge=0.0, le=1.0)

    def is_identity(self) -> bool:
        return (self.brightness == 0 and self.contrast == 1 and self.saturation == 1
                and self.gamma == 1 and self.r == 0 and self.g == 0 and self.b == 0
                and not self.lut_path)


class TimelineClip(BaseModel):
    id: str
    source: str                       # local path (object storage is staged to local first)
    in_s: float = Field(0.0, ge=0.0)
    out_s: Optional[float] = None     # None = end of source
    transition_in: Transition = Field(default_factory=Transition)
    color: ColorAdjust = Field(default_factory=ColorAdjust)
    volume_db: float = 0.0
    mute: bool = False
    label: str = ""
    characters: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_range(self):
        if self.out_s is not None and self.out_s <= self.in_s:
            raise ValueError(f"clip {self.id}: out_s must be > in_s")
        return self


class AudioCue(BaseModel):
    source: str
    start_s: float = Field(0.0, ge=0.0)   # position on the timeline
    in_s: float = Field(0.0, ge=0.0)      # offset into the source
    duration: Optional[float] = None
    gain_db: float = 0.0
    fade_in: float = Field(0.0, ge=0.0)
    fade_out: float = Field(0.0, ge=0.0)


class SubtitleCue(BaseModel):
    start: float = Field(ge=0.0)
    end: float
    text: str
    speaker: Optional[str] = None

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("subtitle end must be after start")
        return self


class OverlayKind(str, Enum):
    title = "title"
    lower_third = "lower_third"
    cta = "cta"
    logo = "logo"
    kinetic = "kinetic"


class Overlay(BaseModel):
    kind: OverlayKind
    start: float = Field(0.0, ge=0.0)
    end: float
    text: str = ""
    subtext: str = ""
    image: Optional[str] = None           # logo png
    position: Literal["top", "center", "bottom", "top_right", "top_left", "bottom_right"] = "bottom"
    color: str = "white"
    accent: str = "0xF5B700"


class AudioMasterSpec(BaseModel):
    target_lufs: float = Field(-14.0, ge=-31.0, le=-5.0)
    true_peak: float = Field(-1.5, ge=-9.0, le=0.0)
    denoise: bool = True
    denoise_nr: float = Field(12.0, ge=0.01, le=97.0)
    voice_eq: bool = True
    compress: bool = True
    duck_music_db: float = Field(-12.0, ge=-30.0, le=0.0)   # music level under voice


class SubtitleStyle(BaseModel):
    font_size: int = Field(64, ge=12, le=200)     # at 1080 width reference
    max_chars_per_line: int = Field(32, ge=10, le=80)
    max_lines: int = Field(2, ge=1, le=3)
    primary: str = "&H00FFFFFF"
    outline: str = "&H00000000"
    outline_w: float = 4.0
    margin_v_pct: float = Field(0.18, ge=0.0, le=0.5)  # safe zone from bottom
    burn_in: bool = True


class ExportFormat(str, Enum):
    vertical = "9:16"
    horizontal = "16:9"
    square = "1:1"
    portrait = "4:5"


class Platform(str, Enum):
    master = "master"
    tiktok = "tiktok"
    youtube_shorts = "youtube_shorts"
    facebook_reels = "facebook_reels"
    zalo = "zalo"
    youtube = "youtube"


class Timeline(BaseModel):
    """The EDL. One timeline == one film."""
    id: str
    title: str = ""
    genre: Genre = Genre.short_film
    width: int = Field(1080, ge=16, le=7680)
    height: int = Field(1920, ge=16, le=7680)
    fps: float = Field(30.0, ge=1, le=120)
    clips: list[TimelineClip]
    music: list[AudioCue] = Field(default_factory=list)
    voice: list[AudioCue] = Field(default_factory=list)
    sfx: list[AudioCue] = Field(default_factory=list)
    subtitles: list[SubtitleCue] = Field(default_factory=list)
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    overlays: list[Overlay] = Field(default_factory=list)
    audio: AudioMasterSpec = Field(default_factory=AudioMasterSpec)
    renditions: list[Platform] = Field(default_factory=lambda: [Platform.tiktok])
    keep_clip_audio: bool = True

    @field_validator("clips")
    @classmethod
    def _non_empty(cls, v):
        if not v:
            raise ValueError("timeline needs at least one clip")
        ids = [c.id for c in v]
        if len(set(ids)) != len(ids):
            raise ValueError("clip ids must be unique")
        return v

    @model_validator(mode="after")
    def _first_clip_has_no_transition(self):
        first = self.clips[0].transition_in
        if first.type != TransitionType.cut or first.duration or first.audio_offset:
            self.clips[0].transition_in = Transition()
        return self


class Severity(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"


class QAIssue(BaseModel):
    code: str
    severity: Severity
    message: str
    start: Optional[float] = None
    end: Optional[float] = None
    clip_id: Optional[str] = None    # lets the engine re-render only this clip
    skill: str = ""


class QAReport(BaseModel):
    target: str
    passed: bool
    issues: list[QAIssue] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)

    @property
    def errors(self) -> list[QAIssue]:
        return [i for i in self.issues if i.severity == Severity.error]
