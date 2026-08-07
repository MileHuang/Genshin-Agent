"""
日程验证工具

检查日程是否存在时间冲突。
"""

from datetime import datetime


TOOL_NAME = "validate_schedule"

TOOL_DESCRIPTION = """
检查生成的日程是否存在时间冲突。
"""


def validate_schedule(
        schedule: list[dict]
) -> dict:
    """
    验证日程。

    Args:
        schedule:
            日程列表

    Returns:
        验证结果
    """

    conflicts = []


    for i in range(len(schedule)):

        for j in range(i + 1, len(schedule)):

            task1 = schedule[i]
            task2 = schedule[j]


            start1 = datetime.strptime(
                task1["开始时间"],
                "%H:%M"
            )

            end1 = datetime.strptime(
                task1["结束时间"],
                "%H:%M"
            )


            start2 = datetime.strptime(
                task2["开始时间"],
                "%H:%M"
            )

            end2 = datetime.strptime(
                task2["结束时间"],
                "%H:%M"
            )


            if start1 < end2 and start2 < end1:

                conflicts.append(
                    f"{task1['任务']} 和 {task2['任务']} 时间冲突"
                )


    return {
        "是否有效": len(conflicts) == 0,
        "冲突": conflicts
    }