"""User feedback events and the local V1 event store.

An EditEvent records facts only. PreferenceAggregator later reads the event
stream to infer behavioral preferences.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


class EditAction(str, Enum):
    """The four plan-feedback actions supported by V1."""

    ACCEPT = "ACCEPT"
    MOVE = "MOVE"
    DELETE = "DELETE"
    SKIP = "SKIP"


@dataclass(frozen=True)
class EditEvent:
    """One user response to a plan item.

    Every action preserves the original interval so later aggregation can
    detect repeated moves or skips. Only MOVE may include a new interval.
    """

    action: EditAction | str
    activity_type: str
    original_start: datetime
    original_end: datetime
    source_plan_id: str
    user_id: str = "default"
    new_start: datetime | None = None
    new_end: datetime | None = None
    reason: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", EditAction(self.action))
        object.__setattr__(
            self, "activity_type", _required_text("activity_type", self.activity_type)
        )
        object.__setattr__(
            self, "source_plan_id", _required_text("source_plan_id", self.source_plan_id)
        )
        object.__setattr__(self, "user_id", _required_text("user_id", self.user_id))
        object.__setattr__(self, "event_id", _required_text("event_id", self.event_id))
        object.__setattr__(self, "reason", _optional_text(self.reason))
        object.__setattr__(
            self,
            "original_start",
            _require_datetime("original_start", self.original_start),
        )
        object.__setattr__(
            self,
            "original_end",
            _require_datetime("original_end", self.original_end),
        )
        object.__setattr__(
            self, "timestamp", _require_datetime("timestamp", self.timestamp)
        )

        if self.original_start >= self.original_end:
            raise ValueError("original_end must be later than original_start")

        if self.action is EditAction.MOVE:
            object.__setattr__(
                self, "new_start", _require_datetime("new_start", self.new_start)
            )
            object.__setattr__(
                self, "new_end", _require_datetime("new_end", self.new_end)
            )
            if self.new_start >= self.new_end:
                raise ValueError("new_end must be later than new_start")
            if (
                self.new_start == self.original_start
                and self.new_end == self.original_end
            ):
                raise ValueError("a MOVE event must change the interval")
        elif self.new_start is not None or self.new_end is not None:
            raise ValueError("only MOVE events may include new_start and new_end")

    def to_dict(self) -> dict[str, Any]:
        """Convert the event to a JSON-compatible dictionary."""

        data = asdict(self)
        data["action"] = self.action.value
        for name in ("original_start", "original_end", "new_start", "new_end", "timestamp"):
            value = data[name]
            data[name] = value.isoformat() if value is not None else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditEvent":
        """Restore an event from a dictionary produced by ``to_dict``."""

        parsed = dict(data)
        for name in ("original_start", "original_end", "new_start", "new_end", "timestamp"):
            if parsed.get(name) is not None:
                value = datetime.fromisoformat(parsed[name])
                # Older local demo files stored naive datetimes. Interpret those
                # using the machine's local timezone during the one-way read.
                parsed[name] = value.astimezone() if value.tzinfo is None else value
        return cls(**parsed)


class EditEventStore:
    """Append-only JSON Lines event store.

    JSONL keeps the local demo inspectable. A future database adapter can
    preserve the same ``append`` and ``list_events`` interface.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: EditEvent) -> None:
        if not isinstance(event, EditEvent):
            raise TypeError("event must be an EditEvent")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def list_events(
        self,
        *,
        user_id: str | None = None,
        activity_type: str | None = None,
        action: EditAction | str | None = None,
    ) -> list[EditEvent]:
        """Read events in append order with optional filters."""

        if not self.path.exists():
            return []

        action_filter = EditAction(action) if action is not None else None
        events: list[EditEvent] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = EditEvent.from_dict(json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"Unable to read EditEvent on line {line_number}") from exc

            if user_id is not None and event.user_id != user_id:
                continue
            if activity_type is not None and event.activity_type != activity_type:
                continue
            if action_filter is not None and event.action is not action_filter:
                continue
            events.append(event)
        return events


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("reason must be a string or None")
    return value.strip() or None


def _require_datetime(name: str, value: datetime | None) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value
