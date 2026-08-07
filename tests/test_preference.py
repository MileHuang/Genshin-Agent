from tools.preference_tool import get_user_preferences


def test_preferences_return_expected_mock_profile():
    preferences = get_user_preferences()

    assert preferences["作息"]["起床时间"] == "08:00"
    assert preferences["作息"]["睡觉时间"] == "23:00"
    assert preferences["运动习惯"] == "晚上运动"
    assert preferences["专注时间"] == "上午"


def test_preferences_are_new_values_on_each_call():
    first = get_user_preferences()
    first["作息"]["起床时间"] = "12:00"

    second = get_user_preferences()
    assert second["作息"]["起床时间"] == "08:00"
