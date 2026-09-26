"""Stable planning contracts shared by agent runtimes, tools, and interfaces."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TextAgent(Protocol):
    """Minimal text-agent interface used by the deterministic offline runtime."""

    async def run(self, prompt: str) -> str: ...


class PlanItem(BaseModel):
    """One executable, non-fixed activity in a daily plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    start_time: str
    end_time: str
    priority: Literal["low", "medium", "high"] = "medium"
    category: str = Field(default="task", min_length=1)
    notes: str = ""

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        try:
            return datetime.strptime(value, "%H:%M").strftime("%H:%M")
        except (TypeError, ValueError) as exc:
            raise ValueError("time values must use 24-hour HH:MM format") from exc

    @model_validator(mode="after")
    def validate_interval(self) -> PlanItem:
        if self.start_time >= self.end_time:
            raise ValueError("end_time must be later than start_time")
        return self

    def to_validator_item(self) -> dict[str, str]:
        return {
            "task": self.title,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }


class PlanDraft(BaseModel):
    """Exact JSON shape expected from the planning model."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1)
    schedule: list[PlanItem] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_chronological_order(self) -> PlanDraft:
        starts = [item.start_time for item in self.schedule]
        if starts != sorted(starts):
            raise ValueError("schedule must be in chronological order")
        return self


class PlanValidation(BaseModel):
    is_valid: bool
    conflicts: list[str] = Field(default_factory=list)


class DailyPlan(BaseModel):
    """Validated result delivered to the CLI and Streamlit interfaces."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    date: date
    goal: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    schedule: list[PlanItem] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    calendar_events_considered: int = Field(ge=0)
    todo_items_considered: int = Field(default=0, ge=0)
    memory_preferences_considered: int = Field(default=0, ge=0)
    weather: dict[str, Any] | None = None
    validation: PlanValidation
