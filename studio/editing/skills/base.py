"""Skill registry. Each skill owns exactly one *slot* in the edit; the engine
refuses to run two skills that write the same slot, so an effect is never
applied twice (e.g. colour graded by two skills, or loudness normalised twice).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Type

from pydantic import BaseModel

SLOTS = (
    "style", "cut_points", "transitions", "continuity_check", "color", "audio_master",
    "av_sync", "subtitles", "graphics", "beat_grid", "format", "final_qa",
)


@dataclass(frozen=True)
class SkillSpec:
    name: str
    slot: str
    summary: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    run: Callable[[BaseModel], BaseModel]

    def __post_init__(self):
        if self.slot not in SLOTS:
            raise ValueError(f"unknown slot {self.slot}")

    def __call__(self, payload: BaseModel | dict) -> BaseModel:
        if isinstance(payload, dict):
            payload = self.input_model.model_validate(payload)
        return self.run(payload)

    def schema(self) -> dict:
        return {"name": self.name, "slot": self.slot, "summary": self.summary,
                "input": self.input_model.model_json_schema(),
                "output": self.output_model.model_json_schema()}


class SlotConflict(RuntimeError):
    pass


def check_no_conflict(specs: list[SkillSpec]) -> None:
    seen: dict[str, str] = {}
    for s in specs:
        if s.slot in seen:
            raise SlotConflict(f"{s.name} and {seen[s.slot]} both write slot '{s.slot}'")
        seen[s.slot] = s.name
