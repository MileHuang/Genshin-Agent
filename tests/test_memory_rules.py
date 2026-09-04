from tools.memory_rules import apply_memory_rules


def _gym_preference(*, status="active", attribute="preferred_time_range"):
    return {
        "activity_type": "gym",
        "attribute": attribute,
        "value": {"start": "20:00", "end": "21:00"},
        "confidence": 0.76,
        "evidence_count": 3,
        "status": status,
    }


def _context(preference):
    return {
        "profile": {"wake_time": "08:00", "sleep_time": "23:00"},
        "preferences": [preference],
        "relevant_events": [],
    }


def test_preferred_time_moves_activity_into_free_slot():
    schedule, explanations = apply_memory_rules(
        [
            {
                "title": "Gym",
                "start_time": "18:00",
                "end_time": "19:00",
                "priority": "medium",
                "category": "health",
            }
        ],
        [],
        _context(_gym_preference()),
        goal="Plan a gym session",
    )

    assert schedule[0]["start_time"] == "20:00"
    assert schedule[0]["end_time"] == "21:00"
    assert "preferred range" in explanations[0]


def test_preferred_time_can_swap_one_flexible_item():
    schedule, _ = apply_memory_rules(
        [
            {
                "title": "Gym",
                "start_time": "18:00",
                "end_time": "19:00",
                "priority": "medium",
                "category": "health",
            },
            {
                "title": "Reading",
                "start_time": "20:00",
                "end_time": "21:00",
                "priority": "medium",
                "category": "study",
            },
        ],
        [],
        _context(_gym_preference()),
        goal="Plan gym and reading",
    )

    by_title = {item["title"]: item for item in schedule}
    assert by_title["Gym"]["start_time"] == "20:00"
    assert by_title["Reading"]["start_time"] == "18:00"


def test_paused_preference_does_not_change_schedule():
    schedule, explanations = apply_memory_rules(
        [
            {
                "title": "Gym",
                "start_time": "18:00",
                "end_time": "19:00",
                "priority": "medium",
                "category": "health",
            }
        ],
        [],
        _context(_gym_preference(status="paused")),
        goal="Plan a gym session",
    )

    assert schedule[0]["start_time"] == "18:00"
    assert explanations == []


def test_preferred_time_never_overrides_fixed_calendar_event():
    schedule, explanations = apply_memory_rules(
        [
            {
                "title": "Gym",
                "start_time": "18:00",
                "end_time": "19:00",
                "priority": "medium",
                "category": "health",
            }
        ],
        [{"title": "Meeting", "start_time": "20:00", "end_time": "21:00"}],
        _context(_gym_preference()),
        goal="Plan a gym session",
    )

    assert schedule[0]["start_time"] == "18:00"
    assert explanations == []
