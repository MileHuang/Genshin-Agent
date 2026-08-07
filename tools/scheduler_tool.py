from datetime import datetime, timedelta


def create_schedule(tasks, start_time="09:00"):
    """
    根据任务列表生成日程安排
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


    return schedule