from datetime import datetime, timezone

import pytest

from tools.edit_event import EditAction, EditEvent, EditEventStore


def _time(hour: int) -> datetime:
    return datetime(2026, 8, 18, hour, 0, tzinfo=timezone.utc)


def test_move_event_round_trips_through_store(tmp_path):
    store = EditEventStore(tmp_path / "behavior_history.jsonl")
    event = EditEvent(
        action=EditAction.MOVE,
        activity_type="gym",
        original_start=_time(19),
        original_end=_time(20),
        new_start=_time(20),
        new_end=_time(21),
        source_plan_id="plan-2026-08-18",
        user_id="mike",
    )

    store.append(event)

    saved_events = store.list_events(user_id="mike", activity_type="gym")
    assert len(saved_events) == 1
    assert saved_events[0].action is EditAction.MOVE
    assert saved_events[0].original_start == _time(19)
    assert saved_events[0].new_start == _time(20)
    assert saved_events[0].event_id == event.event_id


@pytest.mark.parametrize("action", [EditAction.ACCEPT, EditAction.DELETE, EditAction.SKIP])
def test_non_move_events_do_not_accept_new_times(action):
    with pytest.raises(ValueError, match="只有 MOVE"):
        EditEvent(
            action=action,
            activity_type="gym",
            original_start=_time(19),
            original_end=_time(20),
            new_start=_time(20),
            new_end=_time(21),
            source_plan_id="plan-2026-08-18",
        )


def test_move_event_requires_a_complete_valid_new_interval():
    with pytest.raises(ValueError, match="new_start"):
        EditEvent(
            action="MOVE",
            activity_type="gym",
            original_start=_time(19),
            original_end=_time(20),
            source_plan_id="plan-2026-08-18",
        )


def test_store_filters_by_action(tmp_path):
    store = EditEventStore(tmp_path / "behavior_history.jsonl")
    common = {
        "activity_type": "gym",
        "original_start": _time(19),
        "original_end": _time(20),
        "source_plan_id": "plan-2026-08-18",
    }
    store.append(EditEvent(action="ACCEPT", **common))
    store.append(EditEvent(action="SKIP", **common))

    events = store.list_events(action="SKIP")
    assert [event.action for event in events] == [EditAction.SKIP]
