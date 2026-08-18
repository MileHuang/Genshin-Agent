from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from memory import BehavioralPreference, EditEvent, ProfileMemory, TimeSlot


def test_moved_edit_records_original_and_new_slots():
    event = EditEvent(
        event_id="edit-1",
        user_id="user-1",
        plan_id="plan-1",
        plan_item_id="item-1",
        plan_date="2026-08-10",
        item_title="Workout",
        item_category="exercise",
        edit_type="moved",
        original_slot=TimeSlot(start_time="18:00", end_time="18:45"),
        new_slot=TimeSlot(start_time="19:00", end_time="19:45"),
        occurred_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert event.new_slot is not None
    assert event.new_slot.start_time == "19:00"
    assert event.model_dump(mode="json")["plan_date"] == "2026-08-10"


def test_moved_edit_requires_both_time_slots():
    with pytest.raises(ValidationError, match="both original_slot and new_slot"):
        EditEvent(
            user_id="user-1",
            plan_id="plan-1",
            plan_item_id="item-1",
            plan_date="2026-08-10",
            item_title="Workout",
            edit_type="moved",
            original_slot=TimeSlot(start_time="18:00", end_time="18:45"),
        )


def test_non_move_edit_rejects_new_slot():
    with pytest.raises(ValidationError, match="only valid for moved edits"):
        EditEvent(
            user_id="user-1",
            plan_id="plan-1",
            plan_item_id="item-1",
            plan_date="2026-08-10",
            item_title="Buy groceries",
            edit_type="deleted",
            new_slot=TimeSlot(start_time="17:00", end_time="17:30"),
        )


def test_profile_memory_holds_evidence_backed_preference():
    preference = BehavioralPreference(
        preference_id="preference-1",
        key="preferred_exercise_period",
        value="evening",
        scope="category",
        category="exercise",
        status="candidate",
        evidence_event_ids=["edit-1", "edit-2"],
        evidence_count=2,
        confidence=0.65,
    )

    memory = ProfileMemory(user_id="user-1", preferences=[preference])

    assert memory.preferences[0].value == "evening"
    assert memory.preferences[0].evidence_count == 2


def test_preference_rejects_invalid_confidence():
    with pytest.raises(ValidationError):
        BehavioralPreference(
            key="preferred_exercise_period",
            value="evening",
            evidence_event_ids=["edit-1"],
            evidence_count=1,
            confidence=1.1,
        )


def test_profile_rejects_duplicate_preference_identity():
    common = {
        "key": "preferred_focus_period",
        "scope": "global",
        "evidence_count": 1,
        "confidence": 0.5,
    }
    first = BehavioralPreference(
        **common,
        value="morning",
        evidence_event_ids=["edit-1"],
    )
    second = BehavioralPreference(
        **common,
        value="afternoon",
        evidence_event_ids=["edit-2"],
    )

    with pytest.raises(ValidationError, match="unique identities"):
        ProfileMemory(user_id="user-1", preferences=[first, second])
