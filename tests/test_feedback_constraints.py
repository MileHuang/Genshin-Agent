from datetime import date, time

import pytest

from frontend import apply_schedule_feedback, generate_plan
from main import _parse_time_range, _resolve_move
from planner_agents.contracts import DailyPlan
from tools.edit_event import EditEventStore
from tools.feedback_service import FeedbackService


@pytest.fixture
def plan():
    return generate_plan(
        goal="Plan study", target_date=date(2026, 8, 7), location="", demo_mode=True,
    )


def test_generated_plan_preserves_fixed_events_through_serialization(plan):
    restored = DailyPlan.model_validate_json(plan.model_dump_json())
    assert len(restored.calendar_events) == restored.calendar_events_considered == 2
    assert restored.calendar_events[0].title == "Team check-in"


def test_move_into_fixed_event_has_no_local_or_calendar_side_effects(plan, tmp_path):
    service = FeedbackService(EditEventStore(tmp_path / "events.jsonl"))
    original = plan.model_dump()

    class Calendar:
        def update_event(self, *args):
            pytest.fail("Conflicting move must be rejected before Google Calendar write")

    with pytest.raises(ValueError, match="fixed calendar event"):
        apply_schedule_feedback(
            plan, item_index=0, action="move", feedback_service=service,
            new_start=time(10), new_end=time(10, 20),
            google_calendar=Calendar(), google_event_id="synced-event",
        )
    assert plan.model_dump() == original
    assert service.event_store.list_events() == []


def test_move_adjacent_to_fixed_event_is_allowed(plan, tmp_path):
    service = FeedbackService(EditEventStore(tmp_path / "events.jsonl"))
    apply_schedule_feedback(
        plan, item_index=0, action="move", feedback_service=service,
        new_start=time(10, 30), new_end=time(11),
    )
    assert plan.schedule[0].start_time == "10:30"
    assert len(service.event_store.list_events()) == 1


def test_legacy_plan_without_fixed_event_snapshot_requires_regeneration(plan, tmp_path):
    plan.calendar_events = []
    service = FeedbackService(EditEventStore(tmp_path / "events.jsonl"))
    with pytest.raises(ValueError, match="Generate a new plan"):
        apply_schedule_feedback(
            plan, item_index=0, action="move", feedback_service=service,
            new_start=time(10, 30), new_end=time(11),
        )
    assert service.event_store.list_events() == []


def test_cli_move_into_fixed_event_is_rejected_without_prompt(plan, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Must not ask to move a fixed event"))
    start, end = _parse_time_range(plan.date, "10:00-10:20")
    assert _resolve_move(plan, plan.schedule[0], start, end) is None


def test_cli_conflict_resolution_cannot_move_other_item_into_fixed_event(plan, monkeypatch):
    monkeypatch.setattr("main.get_memory_context", lambda *_: {"preferences": []})
    answers = iter(["y", "10:00-10:20"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    original = plan.model_dump()
    start, end = _parse_time_range(plan.date, "12:00-12:30")
    assert _resolve_move(plan, plan.schedule[0], start, end) is None
    assert plan.model_dump() == original
