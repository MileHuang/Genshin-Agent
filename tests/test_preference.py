from datetime import datetime, timezone

from tools.edit_event import EditEvent, EditEventStore
from tools.preference_tool import get_user_preferences


def test_preferences_return_expected_mock_profile():
    preferences = get_user_preferences()

    assert preferences["作息"]["起床时间"] == "08:00"
    assert preferences["作息"]["睡觉时间"] == "23:00"
    assert preferences["运动习惯"] == "晚上运动"
    assert preferences["专注时间"] == "上午"


def test_preferences_are_new_values_on_each_call():
    first = get_user_preferences()
    first["作息"]["起床时间"] = "12:00"

    second = get_user_preferences()
    assert second["作息"]["起床时间"] == "08:00"


def test_preferences_include_learned_time_preference_from_behavior_history(tmp_path):
    store = EditEventStore(tmp_path / "behavior_history.jsonl")
    for day in (10, 12, 15):
        store.append(
            EditEvent(
                action="MOVE",
                activity_type="gym",
                user_id="mike",
                original_start=datetime(2026, 8, day, 19, 0, tzinfo=timezone.utc),
                original_end=datetime(2026, 8, day, 20, 0, tzinfo=timezone.utc),
                new_start=datetime(2026, 8, day, 20, 0, tzinfo=timezone.utc),
                new_end=datetime(2026, 8, day, 21, 0, tzinfo=timezone.utc),
                source_plan_id=f"plan-2026-08-{day:02d}",
            )
        )

    preferences = get_user_preferences(user_id="mike", event_store=store)

    learned = preferences["学习到的偏好"]
    assert len(learned) == 1
    assert learned[0]["activity_type"] == "gym"
    assert learned[0]["attribute"] == "preferred_time_range"
    assert learned[0]["value"] == {"start": "20:00", "end": "21:00"}
    assert learned[0]["evidence_count"] == 3
    assert len(learned[0]["evidence_event_ids"]) == 3
    assert learned[0]["confidence"] == 0.76


def test_preferences_do_not_mix_users(tmp_path):
    store = EditEventStore(tmp_path / "behavior_history.jsonl")
    for user_id in ("mike", "mile"):
        for day in (10, 12, 15):
            store.append(
                EditEvent(
                    action="ACCEPT",
                    activity_type=f"{user_id}-activity",
                    user_id=user_id,
                    original_start=datetime(
                        2026, 8, day, 19, 0, tzinfo=timezone.utc
                    ),
                    original_end=datetime(
                        2026, 8, day, 20, 0, tzinfo=timezone.utc
                    ),
                    source_plan_id=f"{user_id}-plan-{day}",
                )
            )

    preferences = get_user_preferences(user_id="mike", event_store=store)

    learned = preferences["学习到的偏好"]
    assert [preference["activity_type"] for preference in learned] == [
        "mike-activity"
    ]
