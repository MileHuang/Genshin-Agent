from tools.scheduler_tool import create_schedule


if __name__ == "__main__":

    tasks = [
        "学习机器学习",
        "运动",
        "阅读论文"
    ]


    result = create_schedule(tasks)


    for item in result["schedule"]:
        print(item)