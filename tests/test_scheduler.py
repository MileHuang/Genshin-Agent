import pytest

from tools.scheduler_tool import create_schedule


def test_scheduler_creates_consecutive_one_hour_blocks():
    result = create_schedule(
        ["Study", "Exercise", "Read"],
        start_time="09:00",
    )

    assert result["schedule"] == [
        {"任务": "Study", "开始时间": "09:00", "结束时间": "10:00"},
        {"任务": "Exercise", "开始时间": "10:00", "结束时间": "11:00"},
        {"任务": "Read", "开始时间": "11:00", "结束时间": "12:00"},
    ]


def test_scheduler_handles_empty_tasks():
    assert create_schedule([]) == {"schedule": []}


def test_scheduler_rejects_invalid_start_time():
    with pytest.raises(ValueError):
        create_schedule(["Study"], start_time="morning")
