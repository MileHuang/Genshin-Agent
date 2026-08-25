from datetime import date, datetime

import pytest

from tools.calendar_tool import (
    GoogleCalendarProvider,
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


class _FakeRequest:
    def execute(self):
        return {
            "items": [
                {
                    "id": "google-event-1",
                    "summary": "Team meeting",
                    "start": {"dateTime": "2026-08-07T09:30:00+08:00"},
                    "end": {"dateTime": "2026-08-07T10:15:00+08:00"},
                    "location": "Room A",
                    "description": "Weekly sync",
                },
                {
                    "id": "google-event-2",
                    "summary": "Public holiday",
                    "start": {"date": "2026-08-07"},
                    "end": {"date": "2026-08-08"},
                },
            ]
        }


class _FakeEvents:
    def __init__(self):
        self.kwargs = None

    def list(self, **kwargs):
        self.kwargs = kwargs
        return _FakeRequest()


class _FakeService:
    def __init__(self):
        self.resource = _FakeEvents()

    def events(self):
        return self.resource


class _MutationRequest:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def execute(self):
        return self.payload


class _MutationEvents:
    def __init__(self):
        self.insert_kwargs = None
        self.patch_kwargs = None
        self.delete_kwargs = None

    def insert(self, **kwargs):
        self.insert_kwargs = kwargs
        return _MutationRequest({"id": "created-event"})

    def patch(self, **kwargs):
        self.patch_kwargs = kwargs
        return _MutationRequest()

    def delete(self, **kwargs):
        self.delete_kwargs = kwargs
        return _MutationRequest()


class _MutationService:
    def __init__(self):
        self.resource = _MutationEvents()

    def events(self):
        return self.resource


def test_google_provider_maps_events_without_live_api_calls(monkeypatch, tmp_path):
    provider = GoogleCalendarProvider(
        client_secret_file=tmp_path / "client.json",
        token_file=tmp_path / "token.json",
        timezone_name="Asia/Shanghai",
    )
    fake_service = _FakeService()
    monkeypatch.setattr(provider, "_get_service", lambda: fake_service)

    events = provider.get_events("2026-08-07")

    assert events == [
        {
            "id": "google-event-1",
            "title": "Team meeting",
            "date": "2026-08-07",
            "start_time": "09:30",
            "end_time": "10:15",
            "location": "Room A",
            "notes": "Weekly sync",
            "source": "google_calendar",
        },
        {
            "id": "google-event-2",
            "title": "Public holiday",
            "date": "2026-08-07",
            "start_time": "00:00",
            "end_time": "23:59",
            "location": "",
            "notes": "All-day event",
            "source": "google_calendar",
        },
    ]
    assert fake_service.resource.kwargs["calendarId"] == "primary"
    assert fake_service.resource.kwargs["singleEvents"] is True


def test_google_provider_can_create_update_and_delete_events(monkeypatch, tmp_path):
    provider = GoogleCalendarProvider(
        client_secret_file=tmp_path / "client.json",
        token_file=tmp_path / "token.json",
        timezone_name="Asia/Shanghai",
    )
    service = _MutationService()
    monkeypatch.setattr(provider, "_get_service", lambda: service)
    item = {
        "title": "Study",
        "start_time": "09:00",
        "end_time": "10:00",
        "notes": "Review notes",
    }

    assert provider.create_events("2026-08-25", [item]) == ["created-event"]
    provider.update_event("created-event", "2026-08-25", item)
    provider.delete_event("created-event")

    assert service.resource.insert_kwargs["body"]["summary"] == "Study"
    assert service.resource.patch_kwargs["eventId"] == "created-event"
    assert service.resource.patch_kwargs["body"]["start"]["dateTime"] == "2026-08-25T09:00:00"
    assert service.resource.delete_kwargs["eventId"] == "created-event"
