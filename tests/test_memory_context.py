from datetime import datetime, timezone

from tools.edit_event import EditEventStore
from tools.feedback_service import FeedbackService
from tools.memory_context import (
    LocalMemoryContextService,
    MemoryControlStore,
    get_memory_context,
)


def _time(day: int, hour: int) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)


def _service(tmp_path) -> LocalMemoryContextService:
    event_store = EditEventStore(tmp_path / "events.jsonl")
    feedback = FeedbackService(event_store)
    for day in (1, 2, 3):
        feedback.move(
            activity_type="gym",
            original_start=_time(day, 18),
            original_end=_time(day, 19),
            new_start=_time(day, 20),
            new_end=_time(day, 21),
            source_plan_id=f"plan-{day}",
            user_id="member-b",
        )
    return LocalMemoryContextService(
        event_store=event_store,
        control_store=MemoryControlStore(tmp_path / "controls.json"),
    )


def test_memory_context_returns_shared_contract_and_relevant_events(tmp_path):
    service = _service(tmp_path)

    context = get_memory_context(
        "member-b",
        "Plan a gym session",
        service=service,
    )

    assert set(context) == {"profile", "preferences", "relevant_events"}
    assert context["profile"]["focus_period"] == "morning"
    assert len(context["preferences"]) == 1
    assert context["preferences"][0]["status"] == "active"
    assert context["preferences"][0]["evidence_count"] == 3
    assert len(context["relevant_events"]) == 3


def test_user_can_edit_pause_resume_and_forget_learned_memory(tmp_path):
    service = _service(tmp_path)
    preference = service.get_memory_context("member-b", "gym")["preferences"][0]
    memory_id = preference["memory_id"]

    service.update_behavior_preference(
        "member-b",
        memory_id,
        {"start": "21:00", "end": "22:00"},
    )
    service.set_behavior_preference_status("member-b", memory_id, "paused")
    paused = service.get_memory_context("member-b", "gym")["preferences"][0]
    assert paused["value"] == {"start": "21:00", "end": "22:00"}
    assert paused["status"] == "paused"

    service.set_behavior_preference_status("member-b", memory_id, "active")
    active = service.get_memory_context("member-b", "gym")["preferences"][0]
    assert active["status"] == "active"

    service.forget_memory("member-b", memory_id)
    assert service.get_memory_context("member-b", "gym")["preferences"] == []


def test_user_can_update_profile_preference(tmp_path):
    service = _service(tmp_path)

    service.update_profile_preference("member-b", "focus_period", "afternoon")

    context = service.get_memory_context("member-b", "study")
    assert context["profile"]["focus_period"] == "afternoon"
