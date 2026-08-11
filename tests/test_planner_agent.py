import asyncio
import json

import pytest

from planner_agents.planner_agent import (
    PlanValidationError,
    PlannerAgent,
    PlannerOutputError,
    PlannerToolError,
)


class FakeTextAgent:
    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("No fake response remains")
        return self.responses.pop(0)


def plan_json(
    *items: tuple[str, str, str],
    summary: str = "A balanced plan",
) -> str:
    return json.dumps(
        {
            "summary": summary,
            "schedule": [
                {
                    "title": title,
                    "start_time": start,
                    "end_time": end,
                    "priority": "high",
                    "category": "work",
                    "notes": "Stay focused",
                }
                for title, start, end in items
            ],
            "assumptions": ["Tasks can be split into focused blocks."],
        }
    )


def fixed_calendar(_: str) -> list[dict]:
    return [
        {
            "id": "fixed-1",
            "title": "Fixed meeting",
            "date": "2026-08-07",
            "start_time": "10:00",
            "end_time": "10:30",
            "location": "Online",
            "notes": "Cannot move",
            "source": "mock",
        }
    ]


def test_planner_routes_calendar_preferences_and_weather():
    fake_agent = FakeTextAgent(
        plan_json(
            ("Deep work", "09:00", "10:00"),
            ("Outdoor run", "10:30", "11:15"),
        )
    )
    calendar_calls: list[str] = []
    weather_calls: list[tuple[str, str]] = []

    def calendar_getter(target_date: str) -> list[dict]:
        calendar_calls.append(target_date)
        return fixed_calendar(target_date)

    async def weather_getter(location: str, target_date: str) -> dict:
        weather_calls.append((location, target_date))
        return {
            "location": location,
            "date": target_date,
            "condition": "Clear",
            "source": "mock",
        }

    planner = PlannerAgent(
        fake_agent,
        calendar_getter=calendar_getter,
        weather_getter=weather_getter,
        preference_getter=lambda: {"focus_time": "morning"},
    )
    result = asyncio.run(
        planner.create_daily_plan(
            "Finish a report and take an outdoor run",
            target_date="2026-08-07",
            location="Chicago",
        )
    )

    assert result.validation.is_valid is True
    assert result.tools_used == ["calendar", "todo", "preferences", "weather"]
    assert result.todo_items_considered == 3
    assert result.calendar_events_considered == 1
    assert result.weather["condition"] == "Clear"
    assert calendar_calls == ["2026-08-07"]
    assert weather_calls == [("Chicago", "2026-08-07")]
    assert "Fixed meeting" in fake_agent.prompts[0]
    assert "focus_time" in fake_agent.prompts[0]


def test_planner_skips_weather_for_indoor_goal():
    fake_agent = FakeTextAgent(plan_json(("Write report", "09:00", "10:00")))

    def unexpected_weather(*_: object) -> dict:
        raise AssertionError("weather should not be called")

    planner = PlannerAgent(
        fake_agent,
        calendar_getter=lambda _: [],
        weather_getter=unexpected_weather,
        preference_getter=lambda: {},
    )
    result = asyncio.run(
        planner.create_daily_plan(
            "Plan brunch and write an indoor project report",
            target_date="2026-08-07",
            location="Chicago",
        )
    )

    assert result.tools_used == ["calendar", "todo", "preferences"]
    assert result.todo_items_considered == 3
    assert result.weather is None


def test_planner_notes_missing_location_for_weather_sensitive_goal():
    fake_agent = FakeTextAgent(plan_json(("Morning walk", "09:00", "10:00")))
    planner = PlannerAgent(
        fake_agent,
        calendar_getter=lambda _: [],
        preference_getter=lambda: {},
    )

    result = asyncio.run(
        planner.create_daily_plan(
            "Plan an outdoor walk",
            target_date="2026-08-07",
        )
    )

    assert result.weather is None
    assert "weather" not in result.tools_used
    assert any("no location" in item.lower() for item in result.assumptions)


def test_planner_retries_once_after_calendar_conflict():
    fake_agent = FakeTextAgent(
        plan_json(("Conflicting work", "10:00", "11:00")),
        plan_json(("Revised work", "10:30", "11:30")),
    )
    planner = PlannerAgent(
        fake_agent,
        calendar_getter=fixed_calendar,
        preference_getter=lambda: {},
    )

    result = asyncio.run(
        planner.create_daily_plan(
            "Complete focused work",
            target_date="2026-08-07",
        )
    )

    assert result.schedule[0].title == "Revised work"
    assert len(fake_agent.prompts) == 2
    assert "conflicts" in fake_agent.prompts[1]


def test_planner_raises_after_revision_still_conflicts():
    conflict = plan_json(("Conflicting work", "10:00", "11:00"))
    planner = PlannerAgent(
        FakeTextAgent(conflict, conflict),
        calendar_getter=fixed_calendar,
        preference_getter=lambda: {},
    )

    with pytest.raises(PlanValidationError, match="conflict-free"):
        asyncio.run(
            planner.create_daily_plan(
                "Complete focused work",
                target_date="2026-08-07",
            )
        )


@pytest.mark.parametrize(
    "invalid_output",
    [
        "not json",
        '{"summary":"Missing schedule","assumptions":[]}',
        plan_json(("Backwards", "11:00", "10:00")),
    ],
)
def test_planner_rejects_invalid_model_output(invalid_output):
    planner = PlannerAgent(
        FakeTextAgent(invalid_output),
        calendar_getter=lambda _: [],
        preference_getter=lambda: {},
    )

    with pytest.raises(PlannerOutputError):
        asyncio.run(
            planner.create_daily_plan(
                "Plan a work block",
                target_date="2026-08-07",
            )
        )


def test_planner_wraps_tool_failures():
    def failing_calendar(_: str) -> list[dict]:
        raise RuntimeError("calendar unavailable")

    planner = PlannerAgent(
        FakeTextAgent(plan_json(("Work", "09:00", "10:00"))),
        calendar_getter=failing_calendar,
    )

    with pytest.raises(PlannerToolError, match="calendar tool failed"):
        asyncio.run(
            planner.create_daily_plan(
                "Plan work",
                target_date="2026-08-07",
            )
        )


def test_planner_rejects_blank_goal_before_using_tools():
    planner = PlannerAgent(FakeTextAgent())

    with pytest.raises(ValueError, match="goal must not be blank"):
        asyncio.run(planner.create_daily_plan("   "))
