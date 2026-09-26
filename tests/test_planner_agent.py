import asyncio
import json

import pytest

from planner_agents.planner_agent import (
    PlanDraft,
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
    assert result.tools_used == ["calendar", "todo", "memory", "weather"]
    assert result.todo_items_considered == 3
    assert result.calendar_events_considered == 1
    assert result.weather["condition"] == "Clear"
    assert calendar_calls == ["2026-08-07"]
    assert weather_calls == [("Chicago", "2026-08-07")]
    assert "Fixed meeting" in fake_agent.prompts[0]
    assert "focus_time" in fake_agent.prompts[0]


def test_sdk_planner_react_calls_agent_tools_before_model_generation(monkeypatch):
    import agents
    from agents.tool_context import ToolContext

    calendar_calls: list[str] = []
    todo_calls: list[str] = []
    preference_calls: list[str] = []

    def calendar_getter(target_date: str) -> list[dict]:
        calendar_calls.append(target_date)
        return fixed_calendar(target_date)

    def todo_getter() -> list[dict]:
        todo_calls.append("called")
        return [
            {
                "id": "todo-1",
                "title": "Prepare slides",
                "priority": "high",
                "estimated_minutes": 60,
                "deadline": None,
                "category": "work",
                "status": "pending",
            }
        ]

    def preference_getter() -> dict:
        preference_calls.append("called")
        return {"focus_time": "morning"}

    planner = PlannerAgent(
        sdk_model="test-model",
        calendar_getter=calendar_getter,
        todo_getter=todo_getter,
        preference_getter=preference_getter,
    )
    class FakeResult:
        final_output = plan_json(("Prepare slides", "09:00", "10:00"))

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt: str, *, max_turns: int) -> FakeResult:
            assert max_turns == 6
            assert "read-only tools" in prompt
            for tool_name in ("read_calendar", "read_todos", "read_memory"):
                tool = next(tool for tool in agent.tools if tool.name == tool_name)
                await tool.on_invoke_tool(
                    ToolContext(
                        context=None,
                        tool_name=tool_name,
                        tool_call_id=f"{tool_name}-call",
                        tool_arguments="{}",
                    ),
                    "{}",
                )
            return FakeResult()

    monkeypatch.setattr(agents, "Runner", FakeRunner)

    result = asyncio.run(
        planner.create_daily_plan("Prepare presentation", target_date="2026-08-07")
    )

    assert calendar_calls == ["2026-08-07"]
    assert todo_calls == ["called"]
    assert preference_calls == ["called"]
    assert result.tools_used == ["calendar", "todo", "memory"]
    assert result.calendar_events_considered == 1
    assert result.todo_items_considered == 1


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

    assert result.tools_used == ["calendar", "todo", "memory"]
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


def test_planner_wraps_malformed_calendar_event():
    planner = PlannerAgent(
        FakeTextAgent(plan_json(("Work", "09:00", "10:00"))),
        calendar_getter=lambda _: [
            {
                "title": "Broken event",
                "start_time": "11:00",
                "end_time": "10:00",
            }
        ],
        preference_getter=lambda: {},
    )

    with pytest.raises(PlannerToolError, match="invalid event"):
        asyncio.run(
            planner.create_daily_plan(
                "Plan work",
                target_date="2026-08-07",
            )
        )


@pytest.mark.parametrize("invalid_todos", [{"title": "not a list"}, ["not a dict"]])
def test_planner_rejects_malformed_todo_results(invalid_todos):
    planner = PlannerAgent(
        FakeTextAgent(plan_json(("Work", "09:00", "10:00"))),
        calendar_getter=lambda _: [],
        todo_getter=lambda: invalid_todos,
        preference_getter=lambda: {},
    )

    with pytest.raises(PlannerToolError, match="todo"):
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


def test_agents_sdk_path_accepts_text_before_valid_json(monkeypatch):
    import agents

    class FakeResult:
        final_output = f"I'll prepare that.\n{plan_json(('Work', '09:00', '10:00'))}"

    class FakeRunner:
        @staticmethod
        async def run(_: object, __: str, *, max_turns: int) -> FakeResult:
            assert max_turns == 6
            return FakeResult()

    monkeypatch.setattr(agents, "Runner", FakeRunner)
    planner = PlannerAgent()

    draft = asyncio.run(
        planner._generate_draft_with_sdk(object(), "Create a plan")
    )

    assert draft.summary == "A balanced plan"
    assert draft.schedule[0].title == "Work"


def test_sdk_react_path_uses_sdk_function_tools(monkeypatch):
    import agents
    from agents.tool_context import ToolContext

    class FakeResult:
        final_output = plan_json(("Write report", "09:00", "10:00"))

    class FakeRunner:
        @staticmethod
        async def run(agent, _: str, *, max_turns: int) -> FakeResult:
            assert max_turns == 6
            tools = {tool.name: tool for tool in agent.tools}
            await tools["read_calendar"].on_invoke_tool(
                ToolContext(
                    context=None,
                    tool_name="read_calendar",
                    tool_call_id="calendar-call",
                    tool_arguments="{}",
                ),
                "{}",
            )
            await tools["read_memory"].on_invoke_tool(
                ToolContext(
                    context=None,
                    tool_name="read_memory",
                    tool_call_id="memory-call",
                    tool_arguments="{}",
                ),
                "{}",
            )
            return FakeResult()

    monkeypatch.setattr(agents, "Runner", FakeRunner)
    planner = PlannerAgent(
        sdk_model="test-model",
        calendar_getter=lambda _: [],
        todo_getter=lambda: [{"content": "Optional task"}],
        memory_getter=lambda _user_id, _goal: {
            "profile": {},
            "preferences": [],
            "relevant_events": [],
        },
    )

    plan = asyncio.run(
        planner.create_daily_plan("Write a report", target_date="2026-08-07")
    )

    assert plan.tools_used == ["calendar", "memory"]
    assert plan.todo_items_considered == 0


def test_kimi_sdk_agent_uses_text_output_for_compatible_json_parsing():
    planner = PlannerAgent(
        sdk_model="test-model",
        calendar_getter=lambda _: [],
        preference_getter=lambda: {},
    )

    sdk_agent, _, _ = planner._build_sdk_agent(
        "Plan focused work",
        plan_date="2026-08-07",
        location=None,
    )

    assert sdk_agent.output_type is None
