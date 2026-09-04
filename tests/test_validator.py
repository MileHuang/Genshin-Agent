import pytest

from tools.validator_tool import validate_schedule


def test_validator_detects_overlapping_items():
    schedule = [
        {"task": "Study", "start_time": "09:00", "end_time": "11:00"},
        {"task": "Meeting", "start_time": "10:30", "end_time": "12:00"},
    ]

    result = validate_schedule(schedule)

    assert result["is_valid"] is False
    assert result["conflicts"] == ["Study conflicts with Meeting"]


def test_validator_accepts_adjacent_items():
    schedule = [
        {"task": "Study", "start_time": "09:00", "end_time": "10:00"},
        {"task": "Meeting", "start_time": "10:00", "end_time": "11:00"},
    ]

    result = validate_schedule(schedule)

    assert result == {"is_valid": True, "conflicts": []}


def test_validator_accepts_empty_schedule():
    assert validate_schedule([]) == {"is_valid": True, "conflicts": []}


@pytest.mark.parametrize(
    "schedule, message",
    [
        (
            [{"task": "Backwards", "start_time": "11:00", "end_time": "10:00"}],
            "end time must be later",
        ),
        (
            [{"task": "Malformed", "start_time": "9am", "end_time": "10:00"}],
            "HH:MM",
        ),
        (
            [{"task": "Missing end", "start_time": "09:00"}],
            "missing required field",
        ),
    ],
)
def test_validator_rejects_invalid_items(schedule, message):
    with pytest.raises(ValueError, match=message):
        validate_schedule(schedule)
