"""OpenAI Agents SDK adapter for the bounded, read-only planner ReAct loop."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from config.settings import AGENT_SYSTEM_PROMPT, KIMI_BASE_URL, KIMI_MODEL, KIMI_TIMEOUT_SECONDS, MOONSHOT_API_KEY
from planner_agents.context import call_tool, validate_todos
from planner_agents.contracts import PlanDraft
from planner_agents.errors import PlannerConfigurationError, PlannerOutputError, PlannerToolError
from planner_agents.prompts import PLANNER_PROMPT, parse_draft


class ReActPlannerRuntime:
    """Own SDK wiring while leaving provider implementations in the application."""

    def __init__(
        self,
        *,
        sdk_model: Any | None,
        calendar_getter: Callable[..., Any],
        todo_getter: Callable[..., Any],
        weather_getter: Callable[..., Any],
        memory_loader: Callable[[str, str], Awaitable[dict[str, Any]]],
    ) -> None:
        self.sdk_model = sdk_model
        self.calendar_getter = calendar_getter
        self.todo_getter = todo_getter
        self.weather_getter = weather_getter
        self.memory_loader = memory_loader

    def build_agent(
        self, *, goal: str, plan_date: str, location: str | None, user_id: str
    ) -> tuple[Any, dict[str, Any]]:
        try:
            from agents import Agent, OpenAIChatCompletionsModel, function_tool, set_tracing_disabled
        except ImportError as exc:
            raise RuntimeError("OpenAI Agents SDK is not installed. Run `python -m pip install -r requirements.txt`.") from exc

        set_tracing_disabled(disabled=True)
        model = self.sdk_model
        if model is None:
            if not MOONSHOT_API_KEY:
                raise PlannerConfigurationError(
                    "Missing a valid MOONSHOT_API_KEY. Replace the placeholder in .env with your Moonshot API key, or run with --demo."
                )
            model = OpenAIChatCompletionsModel(
                model=KIMI_MODEL,
                openai_client=AsyncOpenAI(api_key=MOONSHOT_API_KEY, base_url=KIMI_BASE_URL, timeout=KIMI_TIMEOUT_SECONDS),
            )

        state: dict[str, Any] = {
            "calendar_events": None, "memory_context": None, "todos": [],
            "weather": None, "tools_used": [],
        }

        def record(name: str) -> None:
            if name not in state["tools_used"]:
                state["tools_used"].append(name)

        @function_tool(name_override="read_calendar", description_override="Read fixed calendar events for the planning date.")
        async def read_calendar() -> str:
            """Return fixed commitments that cannot be moved."""
            if state["calendar_events"] is None:
                events = await call_tool("calendar", self.calendar_getter, plan_date)
                if not isinstance(events, list):
                    raise PlannerToolError("calendar tool must return an event list")
                state["calendar_events"] = events
                record("calendar")
            return json.dumps(state["calendar_events"], ensure_ascii=False, default=str)

        @function_tool(name_override="read_todos", description_override="Read open Todo items when they help plan the goal.")
        async def read_todos() -> str:
            """Return open Todo items."""
            if "todo" not in state["tools_used"]:
                state["todos"] = validate_todos(await call_tool("todo", self.todo_getter))
                record("todo")
            return json.dumps(state["todos"], ensure_ascii=False, default=str)

        @function_tool(name_override="read_memory", description_override="Retrieve goal-relevant profile, preferences, and personal memory.")
        async def read_memory() -> str:
            """Return scoped memory for this planning request."""
            if state["memory_context"] is None:
                state["memory_context"] = await self.memory_loader(user_id, goal)
                record("memory")
            return json.dumps(state["memory_context"], ensure_ascii=False, default=str)

        tools = [read_calendar, read_todos, read_memory]
        if location:
            @function_tool(name_override="read_weather", description_override="Read the forecast for the supplied location and planning date.")
            async def read_weather() -> str:
                """Return weather only when it is useful to the plan."""
                if "weather" not in state["tools_used"]:
                    state["weather"] = await call_tool("weather", self.weather_getter, location, plan_date)
                    record("weather")
                return json.dumps(state["weather"], ensure_ascii=False, default=str)
            tools.append(read_weather)

        instructions = (
            f"{AGENT_SYSTEM_PROMPT}\n\n{PLANNER_PROMPT}\n\n"
            "You are a bounded ReAct planner. You MUST call read_calendar and read_memory before producing a plan. "
            "Call read_todos when open tasks could matter and read_weather only when weather could affect the goal. "
            "All tools are read-only and cached. Use tool output as the only source of external facts, then return only plan JSON."
        )
        return Agent(name="Personal Planner Agent", instructions=instructions, model=model, tools=tools), state

    async def generate(self, agent: Any, prompt: str, *, max_turns: int) -> PlanDraft:
        try:
            from agents import Runner
        except ImportError as exc:
            raise RuntimeError("OpenAI Agents SDK is not installed. Run `python -m pip install -r requirements.txt`.") from exc
        result = await Runner.run(agent, prompt, max_turns=max_turns)
        output = result.final_output
        if isinstance(output, PlanDraft):
            return output
        if isinstance(output, BaseModel):
            output = output.model_dump()
        if isinstance(output, str):
            return parse_draft(output)
        try:
            return PlanDraft.model_validate(output)
        except ValidationError as exc:
            raise PlannerOutputError(f"Agents SDK returned an invalid daily-plan structure: {exc}") from exc
