from datetime import date

from frontend import generate_plan, schedule_rows


def test_demo_frontend_generates_valid_display_rows():
    plan = generate_plan(
        goal="Plan study and an outdoor run",
        target_date=date(2026, 8, 18),
        location="Madison, WI",
        demo_mode=True,
    )

    rows = schedule_rows(plan)

    assert plan.validation.is_valid is True
    assert rows[0]["时间"] == "08:30 – 10:00"
    assert rows[0]["事项"] == "Focused study session"
    assert plan.tools_used == ["calendar", "todo", "preferences", "weather"]
