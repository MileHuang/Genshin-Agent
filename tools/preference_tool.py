"""Preference tool combining a fixed profile with learned behavior."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from tools.edit_event import EditEventStore
from tools.preference_aggregator import PreferenceAggregator


TOOL_NAME = "get_user_preferences"

TOOL_DESCRIPTION = (
    "Return the user's routine, exercise habit, focus period, and learned "
    "behavior preferences."
)


DEFAULT_BEHAVIOR_HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "behavior_history.jsonl"
)

DEFAULT_PROFILE = {
    "timezone": "local",
    "wake_time": "08:00",
    "sleep_time": "23:00",
    "exercise_habit": "evening",
    "focus_period": "morning",
}


def get_user_preferences(
    *,
    user_id: str = "default",
    event_store: EditEventStore | None = None,
    aggregator: PreferenceAggregator | None = None,
) -> dict:
    """Return fixed profile values plus preferences derived from feedback.

    When no behavior history exists, ``learned_preferences`` is empty and the
    planner can continue using the explicit profile values.
    """

    store = event_store or EditEventStore(DEFAULT_BEHAVIOR_HISTORY_PATH)
    preference_aggregator = aggregator or PreferenceAggregator()
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id must not be blank")
    clean_user_id = user_id.strip()
    learned_preferences = [
        preference.to_dict()
        for preference in preference_aggregator.aggregate(
            store.list_events(user_id=clean_user_id)
        )
    ]

    result = deepcopy(DEFAULT_PROFILE)
    result["learned_preferences"] = learned_preferences
    return result
