from datetime import date, datetime

import pytest

from tools.calendar_tool import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    get_calendar_events,
)


def test_calendar_returns_only_events_for_requested_date():
    events = get_calendar_events("2026-08-07")

    assert len(events) == 2
    assert all(event["date"] == "2026-08-07" for event in events)
    assert get_calendar_events("2026-08-09") == []


def test_calendar_accepts_date_objects_and_has_stable_schema():
    events = get_calendar_events(date(2026, 8, 8))

    assert len(events) == 1
    assert set(events[0]) == {
        "id",
        "title",
        "date",
        "start_time",
        "end_time",
        "location",
        "notes",
        "source",
    }
    assert events[0]["source"] == "mock"


def test_calendar_event_intervals_are_valid():
    for event in get_calendar_events("2026-08-07"):
        start = datetime.strptime(event["start_time"], "%H:%M")
        end = datetime.strptime(event["end_time"], "%H:%M")
        assert start < end


def test_calendar_results_are_defensive_copies():
    first = get_calendar_events("2026-08-07")
    first[0]["title"] = "Changed locally"

    second = get_calendar_events("2026-08-07")
    assert second[0]["title"] == "Team check-in"


@pytest.mark.parametrize("invalid_date", ["", "08/07/2026", "not-a-date"])
def test_calendar_rejects_invalid_dates(invalid_date):
    with pytest.raises(ValueError):
        get_calendar_events(invalid_date)


def test_calendar_tool_metadata_is_present():
    assert TOOL_NAME == "get_calendar_events"
    assert TOOL_DESCRIPTION.strip()
