"""Deterministic planning rules derived from the shared memory context."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from typing import Any


def apply_memory_rules(
    schedule: list[dict[str, Any]],
    calendar_events: list[dict[str, Any]],
    memory_context: dict[str, Any],
    *,
    goal: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply active, explainable soft preferences without breaking constraints.

    Calendar events always win. A preferred time may move an item into a free
    slot or swap it with one flexible plan item. Avoid-time rules look for the
    nearest safe slot. Deprioritization never overrides an explicitly requested
    activity.
    """

    planned = deepcopy(schedule)
    events = deepcopy(calendar_events)
    explanations: list[str] = []
    preferences = memory_context.get("preferences", [])
    profile = memory_context.get("profile", {})
    if not isinstance(preferences, list) or not isinstance(profile, dict):
        return planned, explanations

    for preference in preferences:
        if not isinstance(preference, dict) or preference.get("status") == "paused":
            continue
        activity_type = str(preference.get("activity_type", "")).strip()
        attribute = preference.get("attribute")
        value = preference.get("value")
        if not activity_type or not isinstance(value, dict):
            continue

        for index, item in enumerate(planned):
            if not _matches_activity(item, activity_type):
                continue
            if attribute == "preferred_time_range":
                explanation = _apply_preferred_time(
                    planned,
                    events,
                    index,
                    value,
                    activity_type,
                )
            elif attribute == "avoid_time_range":
                explanation = _apply_avoid_time(
                    planned,
                    events,
                    index,
                    value,
                    activity_type,
                    profile,
                )
            elif attribute == "deprioritize_activity":
                explanation = _apply_deprioritization(
                    item,
                    activity_type,
                    goal,
                )
            else:
                explanation = None
            if explanation:
                explanations.append(explanation)
            break

    planned.sort(key=lambda item: str(item.get("start_time", "")))
    return planned, explanations


def _apply_preferred_time(
    schedule: list[dict[str, Any]],
    events: list[dict[str, Any]],
    item_index: int,
    value: dict[str, Any],
    activity_type: str,
) -> str | None:
    preferred = _time_range(value)
    current = _item_range(schedule[item_index])
    if preferred is None or current is None:
        return None
    if preferred[0] <= current[0] and current[1] <= preferred[1]:
        return None

    duration = current[1] - current[0]
    candidate = preferred[0], preferred[0] + duration
    if candidate[1] > preferred[1]:
        return None
    if _move_or_swap(schedule, events, item_index, candidate):
        return (
            f"Applied memory: scheduled {activity_type} within its preferred "
            f"range {_format_range(preferred)}."
        )
    return None


def _apply_avoid_time(
    schedule: list[dict[str, Any]],
    events: list[dict[str, Any]],
    item_index: int,
    value: dict[str, Any],
    activity_type: str,
    profile: dict[str, Any],
) -> str | None:
    avoided = _time_range(value)
    current = _item_range(schedule[item_index])
    if avoided is None or current is None or not _overlap(current, avoided):
        return None

    duration = current[1] - current[0]
    day_start = _clock_minutes(profile.get("wake_time")) or 0
    day_end = _clock_minutes(profile.get("sleep_time")) or 24 * 60
    candidates = [
        (avoided[1], avoided[1] + duration),
        (avoided[0] - duration, avoided[0]),
    ]
    for start in range(day_start, day_end - duration + 1, 15):
        candidates.append((start, start + duration))

    for candidate in candidates:
        if candidate[0] < day_start or candidate[1] > day_end:
            continue
        if _overlap(candidate, avoided):
            continue
        if _move_or_swap(schedule, events, item_index, candidate):
            return (
                f"Applied memory: moved {activity_type} outside its avoided "
                f"range {_format_range(avoided)}."
            )
    return None


def _apply_deprioritization(
    item: dict[str, Any],
    activity_type: str,
    goal: str,
) -> str | None:
    if _goal_mentions(goal, activity_type):
        return None
    if item.get("priority") == "low":
        return None
    item["priority"] = "low"
    return f"Applied memory: lowered the priority of {activity_type}."


def _move_or_swap(
    schedule: list[dict[str, Any]],
    events: list[dict[str, Any]],
    item_index: int,
    candidate: tuple[int, int],
) -> bool:
    if _overlaps_calendar(candidate, events):
        return False
    conflicts = [
        index
        for index, item in enumerate(schedule)
        if index != item_index
        and (item_range := _item_range(item)) is not None
        and _overlap(candidate, item_range)
    ]
    if not conflicts:
        _set_item_range(schedule[item_index], candidate)
        return True
    if len(conflicts) != 1:
        return False

    conflict_index = conflicts[0]
    original = _item_range(schedule[item_index])
    conflict_range = _item_range(schedule[conflict_index])
    if original is None or conflict_range is None:
        return False
    conflict_duration = conflict_range[1] - conflict_range[0]
    replacement = original[0], original[0] + conflict_duration
    if replacement[1] > original[1] or _overlaps_calendar(replacement, events):
        return False
    if any(
        index not in {item_index, conflict_index}
        and (other_range := _item_range(item)) is not None
        and _overlap(replacement, other_range)
        for index, item in enumerate(schedule)
    ):
        return False

    _set_item_range(schedule[item_index], candidate)
    _set_item_range(schedule[conflict_index], replacement)
    return True


def _matches_activity(item: dict[str, Any], activity_type: str) -> bool:
    activity_tokens = set(re.findall(r"[a-z0-9]+", activity_type.casefold()))
    if not activity_tokens:
        return False
    title_tokens = set(
        re.findall(r"[a-z0-9]+", str(item.get("title", "")).casefold())
    )
    category_tokens = set(
        re.findall(r"[a-z0-9]+", str(item.get("category", "")).casefold())
    )
    return activity_tokens <= title_tokens or activity_tokens <= category_tokens


def _goal_mentions(goal: str, activity_type: str) -> bool:
    goal_tokens = set(re.findall(r"[a-z0-9]+", goal.casefold()))
    activity_tokens = set(re.findall(r"[a-z0-9]+", activity_type.casefold()))
    return bool(goal_tokens & activity_tokens)


def _item_range(item: dict[str, Any]) -> tuple[int, int] | None:
    return _time_range(
        {"start": item.get("start_time"), "end": item.get("end_time")}
    )


def _time_range(value: dict[str, Any]) -> tuple[int, int] | None:
    start = _clock_minutes(value.get("start"))
    end = _clock_minutes(value.get("end"))
    if start is None or end is None or start >= end:
        return None
    return start, end


def _clock_minutes(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        return None
    return parsed.hour * 60 + parsed.minute


def _set_item_range(item: dict[str, Any], interval: tuple[int, int]) -> None:
    item["start_time"] = _format_minutes(interval[0])
    item["end_time"] = _format_minutes(interval[1])


def _overlaps_calendar(
    interval: tuple[int, int], events: list[dict[str, Any]]
) -> bool:
    return any(
        (event_range := _item_range(event)) is not None
        and _overlap(interval, event_range)
        for event in events
    )


def _overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]


def _format_minutes(value: int) -> str:
    normalized = value % (24 * 60)
    return f"{normalized // 60:02d}:{normalized % 60:02d}"


def _format_range(interval: tuple[int, int]) -> str:
    return f"{_format_minutes(interval[0])}-{_format_minutes(interval[1])}"
