from tools.validator_tool import validate_schedule


if __name__ == "__main__":

    schedule = [

        {
            "任务": "学习机器学习",
            "开始时间": "09:00",
            "结束时间": "11:00"
        },

        {
            "任务": "开会",
            "开始时间": "10:30",
            "结束时间": "12:00"
        }

    ]


    result = validate_schedule(schedule)


    print(result)