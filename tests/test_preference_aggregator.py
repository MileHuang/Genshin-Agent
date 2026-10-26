from dataclasses import replace
from datetime import datetime, timezone

import pytest

from tools.behavior_preference import BehaviorPreference
from tools.edit_event import EditEvent
from tools.preference_aggregator import PreferenceAggregator


def _event(day: int, new_hour: int, *, action: str = "MOVE") -> EditEvent:
    return EditEvent(
        action=action,
        activity_type="gym",
        original_start=datetime(2026, 8, day, 19, 0, tzinfo=timezone.utc),
        original_end=datetime(2026, 8, day, 20, 0, tzinfo=timezone.utc),
        new_start=(datetime(2026, 8, day, new_hour, 0, tzinfo=timezone.utc) if action == "MOVE" else None),
        new_end=(datetime(2026, 8, day, new_hour + 1, 0, tzinfo=timezone.utc) if action == "MOVE" else None),
        source_plan_id=f"plan-2026-08-{day:02d}",
        user_id="mike",
        timestamp=datetime(2026, 8, day, 22, tzinfo=timezone.utc),
    )


def test_aggregator_creates_time_preference_from_repeated_moves():
    events = [_event(10, 20), _event(12, 20), _event(15, 20)]
    preferences = PreferenceAggregator(minimum_evidence=3).aggregate(events)

    assert len(preferences) == 1
    preference = preferences[0]
    assert preference.category == "scheduling"
    assert preference.activity_type == "gym"
    assert preference.attribute == "preferred_time_range"
    assert preference.value == {"start": "20:00", "end": "21:00"}
    assert preference.evidence_count == 3
    assert preference.evidence_event_ids == tuple(event.event_id for event in events)
    assert preference.confidence >= 0.7


def test_aggregator_does_not_create_preference_with_too_little_evidence():
    preferences = PreferenceAggregator(minimum_evidence=3).aggregate([_event(10, 20), _event(12, 20)])

    assert preferences == []


def test_aggregator_creates_time_preference_from_repeated_accepts():
    preferences = PreferenceAggregator(minimum_evidence=3).aggregate(
        [_event(10, 19, action="ACCEPT"), _event(12, 19, action="ACCEPT"), _event(15, 19, action="ACCEPT")]
    )

    assert preferences[0].attribute == "preferred_time_range"
    assert preferences[0].value == {"start": "19:00", "end": "20:00"}


def test_aggregator_creates_avoid_time_preference_from_repeated_skips():
    preferences = PreferenceAggregator(minimum_evidence=3).aggregate(
        [_event(10, 19, action="SKIP"), _event(12, 19, action="SKIP"), _event(15, 19, action="SKIP")]
    )

    assert preferences[0].attribute == "avoid_time_range"
    assert preferences[0].value == {"start": "19:00", "end": "20:00"}
    assert preferences[0].confidence >= 0.7


def test_aggregator_deprioritizes_activity_after_repeated_deletes():
    preferences = PreferenceAggregator(minimum_evidence=3).aggregate(
        [_event(10, 19, action="DELETE"), _event(12, 19, action="DELETE"), _event(15, 19, action="DELETE")]
    )

    assert preferences[0].category == "planning"
    assert preferences[0].attribute == "deprioritize_activity"
    assert preferences[0].value == {"level": "low"}
    assert preferences[0].confidence >= 0.7


def test_behavior_preference_rejects_backwards_time_range():
    with pytest.raises(ValueError, match="value.end"):
        BehaviorPreference(
            category="scheduling",
            activity_type="gym",
            attribute="preferred_time_range",
            value={"start": "21:00", "end": "20:00"},
            confidence=0.8,
            evidence_count=3,
            evidence_event_ids=("event-1", "event-2", "event-3"),
        )


def test_temporary_exceptions_never_train_or_weaken_a_habit():
    regular = [_event(day, 20) for day in (1, 2, 3)]
    exceptions = [replace(_event(day, 8), scope="temporary") for day in (4, 5, 6)]
    aggregator = PreferenceAggregator()
    assert aggregator.aggregate(exceptions) == []
    result = aggregator.aggregate(regular + exceptions)[0]
    assert result.value == {"start": "20:00", "end": "21:00"}
    assert result.evidence_count == 3
    assert result.confidence == 1.0


def test_new_habit_replaces_old_habit_without_averaging_between_them():
    old = [_event(day, 20) for day in (1, 2, 3)]
    new = [_event(day, 8) for day in (4, 5, 6)]
    result = PreferenceAggregator().aggregate(old + new)[0]
    assert result.value == {"start": "08:00", "end": "09:00"}
    assert result.confidence >= 0.70
    assert result.evidence_event_ids == tuple(event.event_id for event in new)
    assert result.updated_at == new[-1].timestamp
    # Ordering of caller input must not override the chronology.
    assert PreferenceAggregator().aggregate(list(reversed(old + new)))[0].value == result.value


def test_unknown_counterevidence_reduces_old_preference_confidence():
    old = [_event(day, 20) for day in (1, 2, 3)]
    result = PreferenceAggregator().aggregate(old + [_event(4, 8)])[0]
    assert result.value["start"] == "20:00"
    assert result.confidence < 0.70


def test_persistent_declaration_starts_new_epoch_without_three_edits():
    old = [_event(day, 20) for day in (1, 2, 3)]
    declaration = replace(_event(4, 8), scope="persistent")
    result = PreferenceAggregator().aggregate(old + [declaration])[0]
    assert result.value["start"] == "08:00"
    assert result.evidence_event_ids == (declaration.event_id,)
    assert result.confidence == 1.0


def test_opposing_actions_reduce_confidence_and_window_is_bounded():
    old = [_event(day, 20) for day in (1, 2, 3)]
    skipped = [_event(day, 19, action="SKIP") for day in (4, 5, 6)]
    results = PreferenceAggregator().aggregate(old + skipped)
    by_attribute = {item.attribute: item for item in results}
    assert by_attribute["preferred_time_range"].confidence < 0.70
    assert by_attribute["avoid_time_range"].confidence >= 0.70
    assert PreferenceAggregator(window_size=3).aggregate(old + [_event(4, 8)]) == []


def test_duplicate_event_ids_do_not_satisfy_evidence_threshold():
    event = _event(1, 20)
    assert PreferenceAggregator().aggregate([event, event, event]) == []


def test_incompatible_times_do_not_satisfy_evidence_threshold():
    assert PreferenceAggregator().aggregate([_event(1, 8), _event(2, 14), _event(3, 20)]) == []
