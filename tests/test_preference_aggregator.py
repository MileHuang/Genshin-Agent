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
    )


def test_aggregator_creates_time_preference_from_repeated_moves():
    preferences = PreferenceAggregator(minimum_evidence=3).aggregate(
        [_event(10, 20), _event(12, 20), _event(15, 20)]
    )

    assert len(preferences) == 1
    preference = preferences[0]
    assert preference.category == "scheduling"
    assert preference.activity_type == "gym"
    assert preference.attribute == "preferred_time_range"
    assert preference.value == {"start": "20:00", "end": "21:00"}
    assert preference.evidence_count == 3
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
        )
