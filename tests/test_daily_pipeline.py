from tools.preference_tool import get_user_preferences
from tools.scheduler_tool import create_schedule
from tools.validator_tool import validate_schedule


if __name__ == "__main__":


    # Preference
    preferences = get_user_preferences()

    print("用户偏好:")
    print(preferences)



    # Mock Planner
    tasks = [
        "学习机器学习",
        "运动",
        "阅读论文"
    ]


    print("\nPlanner 输出任务:")
    print(tasks)



    # Scheduler
    result = create_schedule(tasks)

    schedule = result["schedule"]


    print("\n生成日程:")

    for item in schedule:
        print(item)



    # Validator
    validation = validate_schedule(schedule)


    print("\n验证结果:")
    print(validation)