"""
日程验证工具

检查日程是否存在时间冲突。
"""

from datetime import datetime
from typing import Any


TOOL_NAME = "validate_schedule"

TOOL_DESCRIPTION = """
检查生成的日程是否存在时间冲突。
"""


def validate_schedule(schedule: list[dict[str, Any]]) -> dict[str, Any]:
    """
    验证日程。

    Args:
        schedule:
            日程列表

    Returns:
        验证结果
    """

    if not isinstance(schedule, list):
        raise ValueError("schedule must be a list")

    normalised: list[tuple[dict[str, Any], datetime, datetime]] = []
    for index, task in enumerate(schedule):
        if not isinstance(task, dict):
            raise ValueError(f"schedule item {index} must be a dictionary")
        missing = [
            key for key in ("任务", "开始时间", "结束时间") if key not in task
        ]
        if missing:
            raise ValueError(
                f"schedule item {index} is missing required field: {missing[0]}"
            )
        if not isinstance(task["任务"], str) or not task["任务"].strip():
            raise ValueError(f"schedule item {index} task name must not be blank")

        try:
            start = datetime.strptime(task["开始时间"], "%H:%M")
            end = datetime.strptime(task["结束时间"], "%H:%M")
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
                    f"{task1['任务']} 和 {task2['任务']} 时间冲突"
                )


    return {
        "是否有效": len(conflicts) == 0,
        "冲突": conflicts
    }
