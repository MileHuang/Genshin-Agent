from datetime import date, time

from frontend import (
    apply_schedule_feedback,
    calendar_sync_key,
    generate_plan,
    get_learning_summary,
    schedule_rows,
)
from tools.edit_event import EditAction, EditEventStore
from tools.feedback_service import FeedbackService


class _RecordingGoogleCalendar:
    def __init__(self):
        self.updated = []
        self.deleted = []

    def update_event(self, event_id, target_date, item):
        self.updated.append((event_id, target_date, item))

    def delete_event(self, event_id):
        self.deleted.append(event_id)


def test_demo_frontend_generates_valid_display_rows():
    plan = generate_plan(
        goal="Plan study and an outdoor run",
        target_date=date(2026, 8, 18),
        location="Madison, WI",
        demo_mode=True,
    )

    rows = schedule_rows(plan)

    assert plan.validation.is_valid is True
    assert rows[0]["时间"] == "08:30 – 10:00"
    assert rows[0]["事项"] == "Focused study session"
    assert plan.tools_used == ["calendar", "todo", "preferences", "weather"]


def test_calendar_sync_key_changes_when_plan_schedule_changes():
    plan = generate_plan(
        goal="Plan study and an outdoor run",
        target_date=date(2026, 8, 18),
        location="Madison, WI",
        demo_mode=True,
    )

    original_key = calendar_sync_key(plan)
    plan.schedule[0].start_time = "09:00"

    assert calendar_sync_key(plan) != original_key


def test_frontend_feedback_persists_skip_and_removes_schedule_item(tmp_path):
    plan = generate_plan(
        goal="Plan study and an outdoor run",
        target_date=date(2026, 8, 18),
        location="Madison, WI",
        demo_mode=True,
    )
    service = FeedbackService(EditEventStore(tmp_path / "events.jsonl"))

    message = apply_schedule_feedback(
        plan,
        item_index=0,
        action="skip",
        feedback_service=service,
    )

    events = service.event_store.list_events()
    assert message.startswith("已记录 SKIP")
    assert len(plan.schedule) == 5
    assert events[0].action is EditAction.SKIP
    assert events[0].activity_type == "focused_study_session"


def test_frontend_feedback_updates_google_for_move_and_delete(tmp_path):
    plan = generate_plan(
        goal="Plan study and an outdoor run",
        target_date=date(2026, 8, 18),
        location="Madison, WI",
        demo_mode=True,
    )
    service = FeedbackService(EditEventStore(tmp_path / "events.jsonl"))
    google = _RecordingGoogleCalendar()

    apply_schedule_feedback(
        plan,
        item_index=0,
        action="move",
        feedback_service=service,
        new_start=time(10, 0),
        new_end=time(11, 0),
        google_calendar=google,
        google_event_id="event-1",
    )
    apply_schedule_feedback(
        plan,
        item_index=0,
        action="delete",
        feedback_service=service,
        google_calendar=google,
        google_event_id="event-2",
    )

    assert google.updated[0][0] == "event-1"
    assert google.updated[0][2]["start_time"] == "10:00"
    assert google.deleted == ["event-2"]


def test_frontend_feedback_rejects_conflicting_move(tmp_path):
    plan = generate_plan(
        goal="Plan study and an outdoor run",
        target_date=date(2026, 8, 18),
        location="Madison, WI",
        demo_mode=True,
    )
    service = FeedbackService(EditEventStore(tmp_path / "events.jsonl"))

    try:
        apply_schedule_feedback(
            plan,
            item_index=0,
            action="move",
            feedback_service=service,
            new_start=time(12, 30),
            new_end=time(13, 30),
        )
    except ValueError as exc:
        assert "冲突" in str(exc)
    else:
        raise AssertionError("Expected a schedule conflict")


def test_learning_summary_forms_preference_after_three_matching_events(tmp_path):
    plan = generate_plan(
        goal="Plan study and an outdoor run",
        target_date=date(2026, 8, 18),
        location="Madison, WI",
        demo_mode=True,
    )
    store = EditEventStore(tmp_path / "events.jsonl")
    service = FeedbackService(store)

    for _ in range(3):
        apply_schedule_feedback(
            plan,
            item_index=0,
            action="accept",
            feedback_service=service,
        )

    summary = get_learning_summary(store)

    assert summary["total_events"] == 3
    assert summary["action_counts"]["ACCEPT"] == 3
    assert summary["evidence"][("focused_study_session", "ACCEPT")] == 3
    assert len(summary["preferences"]) == 1
    assert summary["preferences"][0]["attribute"] == "preferred_time_range"
