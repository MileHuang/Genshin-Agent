import asyncio
import json

from planner_agents.planner_agent import PlannerAgent


class StaticAgent:
    async def run(self, _: str) -> str:
        return json.dumps({
            "summary": "Safe plan",
            "schedule": [{
                "title": "Work", "start_time": "09:00", "end_time": "10:00",
                "priority": "high", "category": "work", "notes": "",
            }],
            "assumptions": [],
        })


def empty_memory(*_: str):
    return {"profile": {}, "preferences": [], "relevant_events": []}


def test_optional_provider_failures_use_visible_fact_free_fallbacks():
    def fail_todos():
        raise RuntimeError("provider offline")

    async def fail_weather(*_: str):
        raise RuntimeError("provider offline")

    def fail_memory(*_: str):
        raise RuntimeError("provider offline")

    planner = PlannerAgent(
        StaticAgent(), calendar_getter=lambda _: [], todo_getter=fail_todos,
        memory_getter=fail_memory, weather_getter=fail_weather,
    )

    plan = asyncio.run(planner.create_daily_plan(
        "Plan work and an outdoor walk", target_date="2026-08-07", location="Madison"
    ))

    assert plan.todo_items_considered == 0
    assert plan.weather == {
        "available": False,
        "location": "Madison",
        "date": "2026-08-07",
        "condition": None,
        "source": "fallback",
        "message": "Weather data is unavailable; do not infer weather conditions.",
    }
    fallbacks = [item for item in plan.tool_trace if item.fallback_applied]
    assert [item.context_label for item in fallbacks] == ["todo", "memory", "weather"]
    assert all("provider offline" not in item.message for item in fallbacks)
    assert any("safe fallback" in assumption for assumption in plan.assumptions)


def test_indoor_goal_avoids_unnecessary_weather_call():
    calls = []

    def weather(*_: str):
        calls.append("weather")
        raise AssertionError("weather should not be selected")

    plan = asyncio.run(PlannerAgent(
        StaticAgent(), calendar_getter=lambda _: [], memory_getter=empty_memory,
        todo_getter=lambda: [], weather_getter=weather,
    ).create_daily_plan("Write an indoor report", target_date="2026-08-07", location="Madison"))

    assert calls == []
    assert "weather" not in plan.tools_used
    assert all(item.context_label != "weather" for item in plan.tool_trace)
