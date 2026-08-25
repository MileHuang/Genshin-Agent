"""Calendar tool with mock and Google Calendar read-only providers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import sys
from typing import Any, Protocol, TypedDict
from zoneinfo import ZoneInfo

from config.settings import (
    CALENDAR_PROVIDER,
    GOOGLE_CALENDAR_ID,
    GOOGLE_CALENDAR_TIMEZONE,
    GOOGLE_CLIENT_SECRET_FILE,
    GOOGLE_TOKEN_FILE,
)


VENDOR_DIR = Path(__file__).resolve().parent.parent / ".vendor"


TOOL_NAME = "get_calendar_events"
TOOL_DESCRIPTION = (
    "Return calendar events for one ISO date. The provider is selected by "
    "CALENDAR_PROVIDER: mock for offline development or google for a "
    "read-only Google Calendar connection."
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
    def get_events(self, target_date: str) -> list[CalendarEvent]: ...


class CalendarConfigurationError(RuntimeError):
    """Raised when local Google Calendar credentials are not ready."""


class CalendarOperationError(RuntimeError):
    """Raised when Google Calendar rejects a create, update, or delete request."""


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
    """In-memory calendar provider used for offline development and tests."""

    def __init__(self, events: Sequence[CalendarEvent] = DEFAULT_MOCK_EVENTS) -> None:
        self._events = deepcopy(list(events))

    def get_events(self, target_date: str) -> list[CalendarEvent]:
        normalised_date = _normalise_date(target_date)
        return deepcopy(
            [event for event in self._events if event["date"] == normalised_date]
        )


class GoogleCalendarProvider:
    """Read fixed events from one Google Calendar through local OAuth."""

    SCOPES = ("https://www.googleapis.com/auth/calendar",)

    def __init__(
        self,
        *,
        calendar_id: str = GOOGLE_CALENDAR_ID,
        client_secret_file: Path = GOOGLE_CLIENT_SECRET_FILE,
        token_file: Path = GOOGLE_TOKEN_FILE,
        timezone_name: str = GOOGLE_CALENDAR_TIMEZONE,
        service_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self.calendar_id = calendar_id
        self.client_secret_file = Path(client_secret_file)
        self.token_file = Path(token_file)
        self.timezone = ZoneInfo(timezone_name)
        self._service_factory = service_factory

    def get_events(self, target_date: str) -> list[CalendarEvent]:
        normalised_date = _normalise_date(target_date)
        day_start = datetime.combine(date.fromisoformat(normalised_date), time.min, self.timezone)
        day_end = day_start + timedelta(days=1)
        response = (
            self._get_service()
            .events()
            .list(
                calendarId=self.calendar_id,
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                timeZone=str(self.timezone),
            )
            .execute()
        )
        return [self._to_calendar_event(item, normalised_date) for item in response.get("items", [])]

    def create_events(self, target_date: str, items: Sequence[dict[str, str]]) -> list[str]:
        """Create confirmed plan items in Google Calendar and return event IDs."""

        target_date = _normalise_date(target_date)
        service = self._get_service()
        created_ids: list[str] = []
        for item in items:
            start_time, end_time = item["start_time"], item["end_time"]
            try:
                event = service.events().insert(
                    calendarId=self.calendar_id,
                    sendUpdates="none",
                    body={
                        "summary": item["title"],
                        "description": item.get("notes", "Created by Personal Planner."),
                        "start": {"dateTime": f"{target_date}T{start_time}:00", "timeZone": str(self.timezone)},
                        "end": {"dateTime": f"{target_date}T{end_time}:00", "timeZone": str(self.timezone)},
                    },
                ).execute()
            except Exception as exc:
                raise CalendarOperationError("无法创建 Google Calendar 日程") from exc
            created_ids.append(str(event["id"]))
        return created_ids

    def update_event(self, event_id: str, target_date: str, item: dict[str, str]) -> None:
        """Update one previously created Personal Planner event."""

        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be a non-blank string")
        target_date = _normalise_date(target_date)
        try:
            self._get_service().events().patch(
                calendarId=self.calendar_id,
                eventId=event_id,
                sendUpdates="none",
                body={
                    "summary": item["title"],
                    "description": item.get("notes", "Created by Personal Planner."),
                    "start": {
                        "dateTime": f"{target_date}T{item['start_time']}:00",
                        "timeZone": str(self.timezone),
                    },
                    "end": {
                        "dateTime": f"{target_date}T{item['end_time']}:00",
                        "timeZone": str(self.timezone),
                    },
                },
            ).execute()
        except CalendarConfigurationError:
            raise
        except Exception as exc:
            raise CalendarOperationError("无法更新 Google Calendar 日程") from exc

    def delete_event(self, event_id: str) -> None:
        """Delete one previously created Personal Planner event."""

        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be a non-blank string")
        try:
            self._get_service().events().delete(
                calendarId=self.calendar_id,
                eventId=event_id,
                sendUpdates="none",
            ).execute()
        except CalendarConfigurationError:
            raise
        except Exception as exc:
            raise CalendarOperationError("无法删除 Google Calendar 日程") from exc

    def _get_service(self) -> Any:
        _enable_local_vendor_packages()
        credentials = self._load_credentials()
        if self._service_factory is not None:
            return self._service_factory(credentials)

        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise CalendarConfigurationError(
                "Google Calendar dependencies are missing. Run: "
                "python -m pip install -r requirements.txt"
            ) from exc
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def _load_credentials(self) -> Any:
        _enable_local_vendor_packages()
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise CalendarConfigurationError(
                "Google Calendar dependencies are missing. Run: "
                "python -m pip install -r requirements.txt"
            ) from exc

        credentials = None
        if self.token_file.exists() and self._stored_token_has_scopes():
            credentials = Credentials.from_authorized_user_file(
                str(self.token_file), self.SCOPES
            )
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            if not self.client_secret_file.exists():
                raise CalendarConfigurationError(
                    f"Google OAuth file not found: {self.client_secret_file}"
                )
            credentials = InstalledAppFlow.from_client_secrets_file(
                str(self.client_secret_file), self.SCOPES
            ).run_local_server(port=0)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    def _stored_token_has_scopes(self) -> bool:
        """Avoid reusing a token that was granted only the old read-only scope."""

        try:
            payload = json.loads(self.token_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        raw_scopes = payload.get("scopes", payload.get("scope", []))
        if isinstance(raw_scopes, str):
            granted_scopes = set(raw_scopes.split())
        elif isinstance(raw_scopes, list):
            granted_scopes = {scope for scope in raw_scopes if isinstance(scope, str)}
        else:
            return False
        return set(self.SCOPES).issubset(granted_scopes)

    def _to_calendar_event(self, item: dict[str, Any], target_date: str) -> CalendarEvent:
        start = item.get("start", {})
        end = item.get("end", {})
        if "date" in start:  # Google represents all-day events with dates only.
            start_time, end_time = "00:00", "23:59"
            notes = _join_notes(item.get("description"), "All-day event")
        else:
            start_at = _parse_google_datetime(start.get("dateTime"))
            end_at = _parse_google_datetime(end.get("dateTime"))
            start_time, end_time = start_at.strftime("%H:%M"), end_at.strftime("%H:%M")
            notes = item.get("description", "")

        return {
            "id": item.get("id", "google-calendar-event"),
            "title": item.get("summary") or "Untitled calendar event",
            "date": target_date,
            "start_time": start_time,
            "end_time": end_time,
            "location": item.get("location", ""),
            "notes": notes,
            "source": "google_calendar",
        }


def get_calendar_events(
    target_date: str | date,
    *,
    provider: CalendarProvider | None = None,
) -> list[CalendarEvent]:
    """Return calendar events for one day from the configured provider."""

    normalised_date = _normalise_date(target_date)
    active_provider = provider or _default_provider()
    return active_provider.get_events(normalised_date)


def _default_provider() -> CalendarProvider:
    if CALENDAR_PROVIDER == "mock":
        return MockCalendarProvider()
    if CALENDAR_PROVIDER == "google":
        return GoogleCalendarProvider()
    raise CalendarConfigurationError(
        "CALENDAR_PROVIDER must be either 'mock' or 'google'."
    )


def _parse_google_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise CalendarConfigurationError("Google Calendar event is missing dateTime.")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _enable_local_vendor_packages() -> None:
    """Allow a local fallback install when the conda environment is read-only."""

    if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))


def _join_notes(description: object, suffix: str) -> str:
    clean_description = description.strip() if isinstance(description, str) else ""
    return f"{clean_description} {suffix}".strip()


def _normalise_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("target_date must be a non-blank ISO date")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("target_date must use YYYY-MM-DD format") from exc
