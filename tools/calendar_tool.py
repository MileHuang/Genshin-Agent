"""Mock calendar tool with a replaceable provider interface."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import date
from typing import Protocol, TypedDict


TOOL_NAME = "get_calendar_events"
TOOL_DESCRIPTION = (
    "Return calendar events for one ISO date. The current provider uses "
    "deterministic mock data and can later be replaced by a real calendar API."
)


class CalendarEvent(TypedDict):
    id: str
    title: str
    date: str
    start_time: str
    end_time: str
    location: str
    notes: str
    source: str


class CalendarProvider(Protocol):
    """Interface implemented by mock and future calendar backends."""

    def get_events(self, target_date: str) -> list[CalendarEvent]: ...


DEFAULT_MOCK_EVENTS: tuple[CalendarEvent, ...] = (
    {
        "id": "mock-calendar-001",
        "title": "Team check-in",
        "date": "2026-08-07",
        "start_time": "10:00",
        "end_time": "10:30",
        "location": "Online",
        "notes": "Fixed calendar commitment",
        "source": "mock",
    },
    {
        "id": "mock-calendar-002",
        "title": "Dentist appointment",
        "date": "2026-08-07",
        "start_time": "15:00",
        "end_time": "16:00",
        "location": "Downtown clinic",
        "notes": "Allow travel time before and after",
        "source": "mock",
    },
    {
        "id": "mock-calendar-003",
        "title": "Project review",
        "date": "2026-08-08",
        "start_time": "13:00",
        "end_time": "14:00",
        "location": "Online",
        "notes": "Review project milestones",
        "source": "mock",
    },
)


class MockCalendarProvider:
    """In-memory calendar provider used until a real API is connected."""

    def __init__(
        self,
        events: Sequence[CalendarEvent] = DEFAULT_MOCK_EVENTS,
    ) -> None:
        self._events = deepcopy(list(events))

    def get_events(self, target_date: str) -> list[CalendarEvent]:
        normalised_date = _normalise_date(target_date)
        return deepcopy(
            [event for event in self._events if event["date"] == normalised_date]
        )


_default_provider: CalendarProvider = MockCalendarProvider()


def get_calendar_events(
    target_date: str | date,
    *,
    provider: CalendarProvider | None = None,
) -> list[CalendarEvent]:
    """Return mock calendar events for ``target_date``.

    The function is intentionally provider-backed so a Google Calendar or
    Microsoft Graph implementation can be substituted without changing the
    planner-facing API.
    """

    normalised_date = _normalise_date(target_date)
    return (provider or _default_provider).get_events(normalised_date)


def _normalise_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("target_date must be a non-blank ISO date")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("target_date must use YYYY-MM-DD format") from exc
