from tools.preference_tool import get_user_preferences
from tools.scheduler_tool import create_schedule
from tools.validator_tool import validate_schedule


if __name__ == "__main__":

    # Step 1:
    # 获取用户偏好
    preferences = get_user_preferences()

    print("用户偏好:")
    print(preferences)


    # Step 2:
    # Mock Planner Agent 输出
    tasks = [
        "学习机器学习",
        "运动",
        "阅读论文"
    ]


    print("\nPlanner 输出任务:")
    print(tasks)


    # Step 3:
    # Scheduler Tool生成日程
    schedule = create_schedule(tasks)


    print("\n生成日程:")
    for item in schedule:
        print(item)


    # Step 4:
    # Validator Tool检查
    result = validate_schedule(schedule)


    print("\n验证结果:")
    print(result)