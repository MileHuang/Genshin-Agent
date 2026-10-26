from datetime import datetime, timezone

import pytest

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


@pytest.mark.parametrize("action", ["accept", "skip", "delete", "move"])
def test_feedback_scope_and_reason_survive_round_trip(tmp_path, action):
    store = EditEventStore(tmp_path / "events.jsonl")
    fields = dict(activity_type="gym", original_start=_time(19),
                  original_end=_time(20), source_plan_id="plan-1",
                  scope="temporary", reason="Dinner tonight")
    if action == "move":
        fields.update(new_start=_time(8), new_end=_time(9))
    event = getattr(FeedbackService(store), action)(**fields)
    assert store.list_events() == [event]
    assert event.scope == "temporary"
    assert event.reason == "Dinner tonight"


def test_invalid_scope_is_rejected_without_writing_event(tmp_path):
    store = EditEventStore(tmp_path / "events.jsonl")
    with pytest.raises(ValueError, match="scope"):
        FeedbackService(store).accept(activity_type="gym", original_start=_time(19),
                                     original_end=_time(20), source_plan_id="plan-1",
                                     scope="forever-ish")
    assert store.list_events() == []
