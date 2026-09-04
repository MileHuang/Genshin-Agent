"""Shared memory-context contract and local adapter for planner integration.

The local adapter keeps Member B development independent from the future
SQLite-backed service. A production service only needs to expose the same
``get_memory_context(user_id, goal)`` shape and management methods.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict
from uuid import NAMESPACE_URL, uuid5

from tools.behavior_preference import BehaviorPreference
from tools.edit_event import EditEvent, EditEventStore
from tools.preference_aggregator import PreferenceAggregator
from tools.preference_tool import DEFAULT_BEHAVIOR_HISTORY_PATH, DEFAULT_PROFILE


DEFAULT_MEMORY_CONTROLS_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "memory_controls.json"
)
MemoryStatus = Literal["active", "paused"]


class MemoryContext(TypedDict):
    """Planner-facing memory response shared by local and durable backends."""

    profile: dict[str, Any]
    preferences: list[dict[str, Any]]
    relevant_events: list[dict[str, Any]]


class MemoryServiceProtocol(Protocol):
    """Operations required by Planner and the user-facing Memory page."""

    def get_memory_context(self, user_id: str, goal: str) -> MemoryContext: ...

    def update_profile_preference(
        self, user_id: str, key: str, value: Any
    ) -> None: ...

    def update_behavior_preference(
        self, user_id: str, memory_id: str, value: dict[str, str]
    ) -> None: ...

    def set_behavior_preference_status(
        self, user_id: str, memory_id: str, status: MemoryStatus
    ) -> None: ...

    def forget_memory(self, user_id: str, memory_id: str) -> None: ...


class MemoryControlStore:
    """Small JSON control store for profile edits and learned-memory controls.

    Raw feedback remains append-only. This store overlays user-authored edits,
    pause states, and forget markers on the derived preference projection.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def user_state(self, user_id: str) -> dict[str, Any]:
        clean_user_id = _required_text("user_id", user_id)
        state = self._load()
        user_state = state.get("users", {}).get(clean_user_id, {})
        return deepcopy(user_state) if isinstance(user_state, dict) else {}

    def update_profile(self, user_id: str, key: str, value: Any) -> None:
        clean_key = _required_text("profile key", key)
        state, user_state = self._mutable_user_state(user_id)
        profile = user_state.setdefault("profile", {})
        if not isinstance(profile, dict):
            raise ValueError("Stored profile controls must be an object")
        profile[clean_key] = value
        self._save(state)

    def update_preference(
        self,
        user_id: str,
        memory_id: str,
        *,
        value: dict[str, str] | None = None,
        status: MemoryStatus | None = None,
        forgotten: bool | None = None,
    ) -> None:
        clean_memory_id = _required_text("memory_id", memory_id)
        state, user_state = self._mutable_user_state(user_id)
        preferences = user_state.setdefault("preferences", {})
        if not isinstance(preferences, dict):
            raise ValueError("Stored preference controls must be an object")
        control = preferences.setdefault(clean_memory_id, {})
        if not isinstance(control, dict):
            raise ValueError("Stored preference control must be an object")
        if value is not None:
            control["value"] = deepcopy(value)
        if status is not None:
            if status not in {"active", "paused"}:
                raise ValueError("status must be 'active' or 'paused'")
            control["status"] = status
        if forgotten is not None:
            control["forgotten"] = bool(forgotten)
        self._save(state)

    def _mutable_user_state(
        self, user_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        clean_user_id = _required_text("user_id", user_id)
        state = self._load()
        users = state.setdefault("users", {})
        if not isinstance(users, dict):
            raise ValueError("Memory control file must contain a users object")
        user_state = users.setdefault(clean_user_id, {})
        if not isinstance(user_state, dict):
            raise ValueError("Stored user memory controls must be an object")
        return state, user_state

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"users": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read memory controls: {self.path}") from exc
        if not isinstance(data, dict):
            raise ValueError("Memory control file must contain a JSON object")
        return data

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.path)


class LocalMemoryContextService:
    """Adapt JSONL feedback and deterministic aggregation to the shared API."""

    def __init__(
        self,
        *,
        event_store: EditEventStore | None = None,
        control_store: MemoryControlStore | None = None,
        aggregator: PreferenceAggregator | None = None,
        profile_factory: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.event_store = event_store or EditEventStore(
            DEFAULT_BEHAVIOR_HISTORY_PATH
        )
        self.control_store = control_store or MemoryControlStore(
            DEFAULT_MEMORY_CONTROLS_PATH
        )
        self.aggregator = aggregator or PreferenceAggregator()
        self.profile_factory = profile_factory or (lambda: deepcopy(DEFAULT_PROFILE))

    def get_memory_context(self, user_id: str, goal: str) -> MemoryContext:
        clean_user_id = _required_text("user_id", user_id)
        if not isinstance(goal, str):
            raise ValueError("goal must be a string")

        events = self.event_store.list_events(user_id=clean_user_id)
        preferences = self.aggregator.aggregate(events)
        controls = self.control_store.user_state(clean_user_id)
        profile = self.profile_factory()
        profile_overrides = controls.get("profile", {})
        if isinstance(profile_overrides, dict):
            profile.update(deepcopy(profile_overrides))

        preference_controls = controls.get("preferences", {})
        if not isinstance(preference_controls, dict):
            preference_controls = {}
        projected_preferences = self._project_preferences(
            clean_user_id,
            preferences,
            preference_controls,
        )

        return {
            "profile": profile,
            "preferences": projected_preferences,
            "relevant_events": [
                event.to_dict() for event in _relevant_events(events, goal)
            ],
        }

    def update_profile_preference(
        self, user_id: str, key: str, value: Any
    ) -> None:
        self.control_store.update_profile(user_id, key, value)

    def update_behavior_preference(
        self, user_id: str, memory_id: str, value: dict[str, str]
    ) -> None:
        preference = self._find_preference(user_id, memory_id)
        BehaviorPreference(
            category=preference.category,
            activity_type=preference.activity_type,
            attribute=preference.attribute,
            value=value,
            confidence=preference.confidence,
            evidence_count=preference.evidence_count,
            evidence_event_ids=preference.evidence_event_ids,
            user_id=preference.user_id,
            preference_id=preference.preference_id,
            created_at=preference.created_at,
            updated_at=preference.updated_at,
        )
        self.control_store.update_preference(
            user_id,
            memory_id,
            value=value,
            forgotten=False,
        )

    def set_behavior_preference_status(
        self, user_id: str, memory_id: str, status: MemoryStatus
    ) -> None:
        self._find_preference(user_id, memory_id)
        self.control_store.update_preference(
            user_id,
            memory_id,
            status=status,
            forgotten=False,
        )

    def forget_memory(self, user_id: str, memory_id: str) -> None:
        self._find_preference(user_id, memory_id)
        self.control_store.update_preference(
            user_id,
            memory_id,
            forgotten=True,
        )

    def _find_preference(
        self, user_id: str, memory_id: str
    ) -> BehaviorPreference:
        clean_user_id = _required_text("user_id", user_id)
        clean_memory_id = _required_text("memory_id", memory_id)
        events = self.event_store.list_events(user_id=clean_user_id)
        for preference in self.aggregator.aggregate(events):
            if _memory_id(preference) == clean_memory_id:
                return preference
        raise KeyError(f"Unknown learned memory: {clean_memory_id}")

    @staticmethod
    def _project_preferences(
        user_id: str,
        preferences: list[BehaviorPreference],
        controls: dict[str, Any],
    ) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for preference in preferences:
            memory_id = _memory_id(preference)
            control = controls.get(memory_id, {})
            if not isinstance(control, dict):
                control = {}
            if control.get("forgotten") is True:
                continue
            data = preference.to_dict()
            data["memory_id"] = memory_id
            data["status"] = (
                control.get("status")
                if control.get("status") in {"active", "paused"}
                else "active"
            )
            if isinstance(control.get("value"), dict):
                data["value"] = deepcopy(control["value"])
            data["user_id"] = user_id
            projected.append(data)
        return projected


def get_memory_context(
    user_id: str,
    goal: str,
    *,
    service: MemoryServiceProtocol | None = None,
) -> MemoryContext:
    """Return profile, explainable preferences, and goal-relevant events."""

    memory_service = service or LocalMemoryContextService()
    return memory_service.get_memory_context(user_id, goal)


def _memory_id(preference: BehaviorPreference) -> str:
    identity = ":".join(
        (
            preference.user_id,
            preference.category,
            preference.activity_type,
            preference.attribute,
        )
    )
    return str(uuid5(NAMESPACE_URL, f"genshin-agent-memory:{identity}"))


def _relevant_events(events: list[EditEvent], goal: str) -> list[EditEvent]:
    if not goal.strip():
        return events[-20:]
    goal_tokens = set(re.findall(r"[a-z0-9]+", goal.casefold()))
    relevant = []
    for event in events:
        activity_tokens = set(re.findall(r"[a-z0-9]+", event.activity_type.casefold()))
        if goal_tokens & activity_tokens:
            relevant.append(event)
    return relevant[-20:]


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()
