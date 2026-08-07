"""
用户偏好工具

用于获取用户长期生活习惯，
帮助 Agent 生成个性化计划。
"""


TOOL_NAME = "get_user_preferences"

TOOL_DESCRIPTION = """
获取用户的生活习惯和偏好信息。
包括作息时间、运动习惯、专注时间。
"""


def get_user_preferences() -> dict:
    """
    获取用户偏好。

    Returns:
        dict:
        用户生活习惯信息
    """

    return {
        "作息": {
            "起床时间": "08:00",
            "睡觉时间": "23:00"
        },

        "运动习惯": "晚上运动",

        "专注时间": "上午"
    }