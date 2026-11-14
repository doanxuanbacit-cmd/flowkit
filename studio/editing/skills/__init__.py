"""The 12 Professional Editing Engine skills, keyed by name."""
from studio.editing.skills import (
    edit_audio_master, edit_auto_subtitles, edit_beat_sync, edit_color_grading, edit_continuity,
    edit_dialogue_sync, edit_director, edit_master_qa, edit_motion_graphics, edit_multi_format,
    edit_smart_cut, edit_transition_pro,
)
from studio.editing.skills.base import SkillSpec, SlotConflict, check_no_conflict

ALL: list[SkillSpec] = [
    edit_director.SPEC, edit_smart_cut.SPEC, edit_transition_pro.SPEC, edit_continuity.SPEC,
    edit_color_grading.SPEC, edit_audio_master.SPEC, edit_dialogue_sync.SPEC, edit_auto_subtitles.SPEC,
    edit_motion_graphics.SPEC, edit_beat_sync.SPEC, edit_multi_format.SPEC, edit_master_qa.SPEC,
]
REGISTRY: dict[str, SkillSpec] = {s.name: s for s in ALL}
check_no_conflict(ALL)

__all__ = ["ALL", "REGISTRY", "SkillSpec", "SlotConflict", "check_no_conflict"]
