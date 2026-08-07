"""
日程生成工具

根据任务列表生成时间安排。
"""

from datetime import datetime, timedelta


TOOL_NAME = "create_schedule"

TOOL_DESCRIPTION = """
根据用户任务列表生成一天的时间安排。
"""


def create_schedule(
        tasks: list[str],
        start_time: str = "09:00"
) -> dict:
    """
    创建日程。

    Args:
        tasks:
            用户需要完成的任务

        start_time:
            开始时间

    Returns:
        包含schedule的字典
    """

    schedule = []

    current_time = datetime.strptime(
        start_time,
        "%H:%M"
    )


    for task in tasks:

        end_time = current_time + timedelta(hours=1)

        schedule.append(
            {
                "任务": task,
                "开始时间": current_time.strftime("%H:%M"),
                "结束时间": end_time.strftime("%H:%M")
            }
        )

        current_time = end_time


    return {
        "schedule": schedule
    }