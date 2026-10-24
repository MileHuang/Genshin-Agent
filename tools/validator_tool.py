"""Deterministic schedule conflict validator."""

from datetime import datetime
from typing import Any


TOOL_NAME = "validate_schedule"

TOOL_DESCRIPTION = "Check a generated schedule for time conflicts."


def validate_schedule(schedule: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate schedule fields, intervals, and pairwise overlaps."""

    if not isinstance(schedule, list):
        raise ValueError("schedule must be a list")

    normalised: list[tuple[dict[str, Any], datetime, datetime]] = []
    for index, task in enumerate(schedule):
        if not isinstance(task, dict):
            raise ValueError(f"schedule item {index} must be a dictionary")
        missing = [
            key for key in ("task", "start_time", "end_time") if key not in task
        ]
        if missing:
            raise ValueError(
                f"schedule item {index} is missing required field: {missing[0]}"
            )
        if not isinstance(task["task"], str) or not task["task"].strip():
            raise ValueError(f"schedule item {index} task name must not be blank")

        try:
            start = datetime.strptime(task["start_time"], "%H:%M")
            end = datetime.strptime(task["end_time"], "%H:%M")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"schedule item {index} times must use 24-hour HH:MM format"
            ) from exc
        if start >= end:
            raise ValueError(
                f"schedule item {index} end time must be later than start time"
            )
        normalised.append((task, start, end))

    conflicts: list[str] = []
    for i, (task1, start1, end1) in enumerate(normalised):
        for task2, start2, end2 in normalised[i + 1:]:
            if start1 < end2 and start2 < end1:
                conflicts.append(
                    f"{task1['task']} conflicts with {task2['task']}"
                )


    return {
        "is_valid": len(conflicts) == 0,
        "conflicts": conflicts,
    }
