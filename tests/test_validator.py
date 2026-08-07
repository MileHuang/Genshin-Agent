from tools.validator_tool import validate_schedule


def test_validator_detects_overlapping_items():
    schedule = [
        {"任务": "Study", "开始时间": "09:00", "结束时间": "11:00"},
        {"任务": "Meeting", "开始时间": "10:30", "结束时间": "12:00"},
    ]

    result = validate_schedule(schedule)

    assert result["是否有效"] is False
    assert result["冲突"] == ["Study 和 Meeting 时间冲突"]


def test_validator_accepts_adjacent_items():
    schedule = [
        {"任务": "Study", "开始时间": "09:00", "结束时间": "10:00"},
        {"任务": "Meeting", "开始时间": "10:00", "结束时间": "11:00"},
    ]

    result = validate_schedule(schedule)

    assert result == {"是否有效": True, "冲突": []}


def test_validator_accepts_empty_schedule():
    assert validate_schedule([]) == {"是否有效": True, "冲突": []}
