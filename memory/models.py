"""Pydantic models for the Phase 1 memory and feedback draft.

These models deliberately define data contracts only. Persistence and preference
inference belong to later tasks so the Daily Planner MVP can stay database-free.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EditType = Literal["accepted", "moved", "deleted", "skipped"]
PreferenceValue = str | int | float | bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TimeSlot(BaseModel):
    """A plan item's start and end time before or after an edit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_time: str
    end_time: str

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        try:
            return datetime.strptime(value, "%H:%M").strftime("%H:%M")
        except (TypeError, ValueError) as exc:
            raise ValueError("time values must use 24-hour HH:MM format") from exc

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        start = datetime.strptime(self.start_time, "%H:%M")
        end = datetime.strptime(self.end_time, "%H:%M")
        if start >= end:
            raise ValueError("end_time must be later than start_time")
        return self


class EditEvent(BaseModel):
    """Immutable evidence describing a user's response to one plan item."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    event_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    user_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_item_id: str = Field(min_length=1)
    plan_date: date
    item_title: str = Field(min_length=1)
    item_category: str = Field(default="task", min_length=1)
    edit_type: EditType
    original_slot: TimeSlot | None = None
    new_slot: TimeSlot | None = None
    reason: str = ""
    occurred_at: datetime = Field(default_factory=_utc_now)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_edit_shape(self) -> Self:
        if self.edit_type == "moved":
            if self.original_slot is None or self.new_slot is None:
                raise ValueError(
                    "moved edits require both original_slot and new_slot"
                )
            if self.original_slot == self.new_slot:
                raise ValueError("moved edits must change the time slot")
        elif self.new_slot is not None:
            raise ValueError("new_slot is only valid for moved edits")
        return self


class BehavioralPreference(BaseModel):
    """A candidate or active habit inferred from repeated edit evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    preference_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    key: str = Field(min_length=1)
    value: PreferenceValue
    scope: Literal["global", "category"] = "global"
    category: str | None = None
    status: Literal["candidate", "active"] = "candidate"
    evidence_event_ids: list[str] = Field(min_length=1)
    evidence_count: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    last_updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("last_updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("last_updated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        unique_evidence = set(self.evidence_event_ids)
        if len(unique_evidence) != len(self.evidence_event_ids):
            raise ValueError("evidence_event_ids must be unique")
        if self.evidence_count < len(unique_evidence):
            raise ValueError(
                "evidence_count cannot be smaller than the supplied evidence IDs"
            )
        if self.scope == "category" and not self.category:
            raise ValueError("category-scoped preferences require a category")
        if self.scope == "global" and self.category is not None:
            raise ValueError("global preferences cannot specify a category")
        return self

    @property
    def identity(self) -> tuple[str, str, str | None]:
        """Return the fields that uniquely identify a learned preference."""

        return self.scope, self.key, self.category


class ProfileMemory(BaseModel):
    """Versioned snapshot of behavioral preferences for one user."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1)
    preferences: list[BehavioralPreference] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("memory timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        identities = [preference.identity for preference in self.preferences]
        if len(set(identities)) != len(identities):
            raise ValueError("profile preferences must have unique identities")
        return self
