"""Structured preferences inferred from repeated behavior events."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class BehaviorPreference:
    """One behavioral preference that the planner can consume directly.

    Time preferences use ``{"start": "HH:MM", "end": "HH:MM"}``.
    Priority preferences use a small structured value such as
    ``{"level": "low"}``.
    """

    category: str
    activity_type: str
    attribute: str
    value: dict[str, str]
    confidence: float
    evidence_count: int
    evidence_event_ids: tuple[str, ...]
    user_id: str = "default"
    preference_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", _required_text("category", self.category))
        object.__setattr__(
            self, "activity_type", _required_text("activity_type", self.activity_type)
        )
        object.__setattr__(self, "attribute", _required_text("attribute", self.attribute))
        object.__setattr__(self, "user_id", _required_text("user_id", self.user_id))
        object.__setattr__(
            self, "preference_id", _required_text("preference_id", self.preference_id)
        )
        if not isinstance(self.evidence_count, int) or self.evidence_count < 1:
            raise ValueError("evidence_count must be a positive integer")
        if not isinstance(self.confidence, (int, float)) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", float(self.confidence))
        if not isinstance(self.evidence_event_ids, tuple):
            raise ValueError("evidence_event_ids must be a tuple")
        if len(set(self.evidence_event_ids)) != len(self.evidence_event_ids):
            raise ValueError("evidence_event_ids must be unique")
        if len(self.evidence_event_ids) != self.evidence_count:
            raise ValueError("evidence_count must equal the number of event IDs")
        if any(not isinstance(event_id, str) or not event_id.strip() for event_id in self.evidence_event_ids):
            raise ValueError("evidence_event_ids must contain non-blank strings")
        if self.attribute in {"preferred_time_range", "avoid_time_range"}:
            _validate_time_range(self.value)
        else:
            _validate_structured_value(self.value)
        if not isinstance(self.created_at, datetime) or not isinstance(self.updated_at, datetime):
            raise ValueError("created_at and updated_at must be datetimes")

    def to_dict(self) -> dict[str, Any]:
        """Convert the preference to a JSON-compatible planner structure."""

        data = asdict(self)
        data["evidence_event_ids"] = list(self.evidence_event_ids)
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()


def _validate_time_range(value: dict[str, str]) -> None:
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError('value must contain exactly "start" and "end"')
    parsed_minutes: dict[str, int] = {}
    for name in ("start", "end"):
        time = value[name]
        if not isinstance(time, str) or len(time) != 5 or time[2] != ":":
            raise ValueError(f"value.{name} must use HH:MM format")
        try:
            hours, minutes = (int(part) for part in time.split(":"))
        except ValueError as exc:
            raise ValueError(f"value.{name} must use HH:MM format") from exc
        if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
            raise ValueError(f"value.{name} must be a valid time")
        parsed_minutes[name] = hours * 60 + minutes
    if parsed_minutes["start"] >= parsed_minutes["end"]:
        raise ValueError("value.end must be later than value.start")


def _validate_structured_value(value: dict[str, str]) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError("value must be a non-empty dictionary")
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise ValueError("value keys and values must be strings")
