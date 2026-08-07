# tools/validator_tool.py


from datetime import datetime


def validate_schedule(schedule):
    """
    检查日程是否存在时间冲突

    Args:
        schedule:
            Scheduler Tool 输出的日程列表

    Returns:
        {
            "是否有效": True/False,
            "冲突": []
        }
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


            # 判断时间重叠
            if start1 < end2 and start2 < end1:

                conflicts.append(
                    f"{task1['任务']} 和 {task2['任务']} 时间冲突"
                )


    return {
        "是否有效": len(conflicts) == 0,
        "冲突": conflicts
    }