from datetime import datetime, timezone

from tools.edit_event import EditAction, EditEventStore
from tools.feedback_service import FeedbackService


def _time(hour: int) -> datetime:
    return datetime(2026, 8, 18, hour, 0, tzinfo=timezone.utc)


def test_move_feedback_creates_and_persists_edit_event(tmp_path):
    store = EditEventStore(tmp_path / "behavior_history.jsonl")
    feedback = FeedbackService(store)

    event = feedback.move(
        activity_type="gym",
        original_start=_time(19),
        original_end=_time(20),
        new_start=_time(20),
        new_end=_time(21),
        source_plan_id="plan-2026-08-18",
        user_id="mike",
    )

    assert event.action is EditAction.MOVE
    assert store.list_events() == [event]


def test_accept_delete_and_skip_feedback_are_persisted(tmp_path):
    store = EditEventStore(tmp_path / "behavior_history.jsonl")
    feedback = FeedbackService(store)
    common = {
        "activity_type": "gym",
        "original_start": _time(19),
        "original_end": _time(20),
        "source_plan_id": "plan-2026-08-18",
    }

    feedback.accept(**common)
    feedback.delete(**common, reason="No longer needed")
    feedback.skip(**common, reason="Feeling unwell")

    assert [event.action for event in store.list_events()] == [
        EditAction.ACCEPT,
        EditAction.DELETE,
        EditAction.SKIP,
    ]
