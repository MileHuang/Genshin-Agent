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
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, NotRequired, Protocol, TypedDict
from uuid import NAMESPACE_URL, uuid5

from memory.rag import (
    ConsolidatedMemory,
    ConsolidationPreview,
    KimiMemorySummarizer,
    MemoryDocument,
    MemorySummarizer,
    PersonalRagStore,
    configured_embedding_provider,
)
from memory.session_memory import SessionMemoryStore
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
    relevant_memories: NotRequired[list[dict[str, Any]]]
    working_memory: NotRequired[dict[str, Any] | None]


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


class RagMemoryContextService:
    """Compose existing explainable preferences with personal RAG retrieval.

    The local feedback service remains the source of deterministic behavioral
    preference rules.  The RAG store adds source-linked episodic recall without
    allowing vector results to override calendar constraints or active rules.
    """

    def __init__(
        self,
        *,
        base_service: LocalMemoryContextService | None = None,
        rag_store: PersonalRagStore | None = None,
        session_store: SessionMemoryStore | None = None,
        summarizer: MemorySummarizer | None = None,
    ) -> None:
        self.base_service = base_service or LocalMemoryContextService()
        self.rag_store = rag_store or PersonalRagStore(
            embedder=configured_embedding_provider()
        )
        self.session_store = session_store or SessionMemoryStore()
        self.summarizer = summarizer

    def get_memory_context(self, user_id: str, goal: str) -> MemoryContext:
        clean_user_id = _required_text("user_id", user_id)
        if not isinstance(goal, str):
            raise ValueError("goal must be a string")

        self._sync_feedback_events(clean_user_id)
        base_context = self.base_service.get_memory_context(clean_user_id, goal)
        profile = deepcopy(base_context["profile"])
        profile.update(self.rag_store.get_profile(clean_user_id))
        session = self.session_store.load_latest_active(clean_user_id)
        memories = self.rag_store.retrieve(clean_user_id, goal, limit=6) if goal.strip() else []

        return {
            "profile": profile,
            "preferences": base_context["preferences"],
            "relevant_events": [
                event
                for event in base_context["relevant_events"]
                if not self.rag_store.is_source_suppressed(
                    clean_user_id, str(event["event_id"])
                )
                and not self.rag_store.is_source_consolidated(
                    clean_user_id, str(event["event_id"])
                )
            ],
            "relevant_memories": [memory.to_context() for memory in memories],
            "working_memory": (
                {
                    "session_id": session.session_id,
                    "timezone": session.timezone_name,
                    "expires_at": session.expires_at.isoformat(),
                    "content": session.body,
                }
                if session is not None
                else None
            ),
        }

    def update_profile_preference(
        self, user_id: str, key: str, value: Any
    ) -> None:
        self.base_service.update_profile_preference(user_id, key, value)
        if isinstance(value, str):
            self.rag_store.set_profile(user_id, {key: value})

    def update_behavior_preference(
        self, user_id: str, memory_id: str, value: dict[str, str]
    ) -> None:
        self.base_service.update_behavior_preference(user_id, memory_id, value)

    def set_behavior_preference_status(
        self, user_id: str, memory_id: str, status: MemoryStatus
    ) -> None:
        self.base_service.set_behavior_preference_status(user_id, memory_id, status)

    def forget_memory(self, user_id: str, memory_id: str) -> None:
        context = self.base_service.get_memory_context(user_id, "")
        preference = next(
            (
                item
                for item in context["preferences"]
                if item.get("memory_id") == memory_id
            ),
            None,
        )
        if preference is None:
            raise KeyError(f"Unknown learned memory: {memory_id}")
        evidence_ids = preference.get("evidence_event_ids", [])
        self.rag_store.suppress_sources(user_id, evidence_ids)
        self.base_service.forget_memory(user_id, memory_id)

    def begin_session(
        self,
        user_id: str,
        session_id: str,
        goal: str,
        *,
        temporary_constraints: list[str] | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        """Write the current working context without putting it in long-term RAG."""

        clean_user_id = _required_text("user_id", user_id)
        profile = deepcopy(self.base_service.get_memory_context(clean_user_id, "")["profile"])
        profile.update(self.rag_store.get_profile(clean_user_id))
        timezone_name = str(profile.get("timezone", "UTC"))
        self.session_store.save(
            user_id=clean_user_id,
            session_id=session_id,
            timezone_name=timezone_name,
            expires_at=(expires_at or datetime.now(timezone.utc) + timedelta(hours=24)).replace(microsecond=0),
            goal=goal,
            temporary_constraints=temporary_constraints,
        )

    def consolidate_feedback(
        self, user_id: str, *, limit: int = 20
    ) -> dict[str, Any] | None:
        """Programmatically create a summary without a UI review step.

        The Streamlit flow uses ``preview_feedback_consolidation`` followed by
        ``confirm_feedback_consolidation`` so a person always reviews LLM output.
        """

        preview = self.preview_feedback_consolidation(user_id, limit=limit)
        return (
            self.confirm_feedback_consolidation(user_id, preview)
            if preview is not None
            else None
        )

    def preview_feedback_consolidation(
        self, user_id: str, *, limit: int = 20
    ) -> ConsolidationPreview | None:
        """Generate an unsaved Kimi summary for a human to review."""

        clean_user_id = _required_text("user_id", user_id)
        if limit < 2:
            raise ValueError("limit must be at least 2")
        events = [
            event
            for event in self.base_service.event_store.list_events(
                user_id=clean_user_id
            )
            if not self.rag_store.is_source_suppressed(
                clean_user_id, event.event_id
            )
            and not self.rag_store.is_source_consolidated(
                clean_user_id, event.event_id
            )
        ][-limit:]
        if len(events) < 2:
            return None

        summary = (self.summarizer or KimiMemorySummarizer()).summarize(
            clean_user_id, events
        )
        return ConsolidationPreview(
            user_id=clean_user_id,
            source_ids=tuple(event.event_id for event in events),
            summary=summary,
        )

    def confirm_feedback_consolidation(
        self,
        user_id: str,
        preview: ConsolidationPreview,
        *,
        title: str | None = None,
        content: str | None = None,
        importance: float | None = None,
    ) -> dict[str, Any]:
        """Persist a reviewed preview if all of its evidence is still pending."""

        clean_user_id = _required_text("user_id", user_id)
        if not isinstance(preview, ConsolidationPreview):
            raise TypeError("preview must be a ConsolidationPreview")
        if preview.user_id != clean_user_id:
            raise MemoryConsolidationError("This preview belongs to another user.")

        events_by_id = {
            event.event_id: event
            for event in self.base_service.event_store.list_events(user_id=clean_user_id)
        }
        try:
            events = [events_by_id[source_id] for source_id in preview.source_ids]
        except KeyError as exc:
            raise MemoryConsolidationError(
                "The feedback changed; generate a new preview before saving."
            ) from exc
        if any(
            self.rag_store.is_source_suppressed(clean_user_id, event.event_id)
            or self.rag_store.is_source_consolidated(clean_user_id, event.event_id)
            for event in events
        ):
            raise MemoryConsolidationError(
                "The feedback changed; generate a new preview before saving."
            )

        reviewed_summary = ConsolidatedMemory(
            title=(title if title is not None else preview.summary.title).strip(),
            summary=(content if content is not None else preview.summary.summary).strip(),
            importance=(importance if importance is not None else preview.summary.importance),
        )
        source_ids = preview.source_ids
        digest = sha256("|".join(source_ids).encode("utf-8")).hexdigest()[:24]
        document = MemoryDocument(
            memory_id=f"llm-summary:{digest}",
            user_id=clean_user_id,
            memory_type="episode",
            status="active",
            title=reviewed_summary.title,
            content=reviewed_summary.summary,
            source_ids=source_ids,
            tags=("llm-summary",),
            confidence=1.0,
            importance=reviewed_summary.importance,
            occurred_at=max(event.timestamp for event in events),
        )
        self.rag_store.upsert(document)
        self.rag_store.mark_sources_consolidated(clean_user_id, source_ids)
        return {
            "memory_id": document.memory_id,
            "title": document.title,
            "content": document.content,
            "source_ids": list(document.source_ids),
        }

    def _sync_feedback_events(self, user_id: str) -> None:
        """Create source-linked episodic memories from immutable feedback."""

        for event in self.base_service.event_store.list_events(user_id=user_id):
            if (
                self.rag_store.is_source_suppressed(user_id, event.event_id)
                or self.rag_store.is_source_consolidated(user_id, event.event_id)
            ):
                continue
            action = event.action.value.lower()
            content = (
                f"The user {action} the planned {event.activity_type} activity. "
                f"Its original time was {event.original_start.isoformat()} to "
                f"{event.original_end.isoformat()}."
            )
            if event.new_start is not None and event.new_end is not None:
                content += (
                    f" It was moved to {event.new_start.isoformat()} to "
                    f"{event.new_end.isoformat()}."
                )
            if event.reason:
                content += f" User reason: {event.reason}."
            self.rag_store.upsert(
                MemoryDocument(
                    memory_id=f"edit-event:{event.event_id}",
                    user_id=user_id,
                    memory_type="episode",
                    status="active",
                    title=f"Planning feedback: {event.activity_type}",
                    content=content,
                    source_ids=(event.event_id, event.source_plan_id),
                    tags=(event.activity_type, action),
                    confidence=1.0,
                    importance=0.35,
                    occurred_at=event.timestamp,
                )
            )


def get_memory_context(
    user_id: str,
    goal: str,
    *,
    service: MemoryServiceProtocol | None = None,
) -> MemoryContext:
    """Return profile, explainable preferences, and goal-relevant events."""

    # Preserve an offline-compatible default for legacy callers that do not
    # inject a memory service. Production entry points construct
    # RagMemoryContextService directly and use the configured provider.
    memory_service = service or RagMemoryContextService(
        rag_store=PersonalRagStore()
    )
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
