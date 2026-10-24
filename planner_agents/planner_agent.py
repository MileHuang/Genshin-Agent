"""Compatibility facade for daily planning orchestration.

The facade keeps the established public API.  SDK ReAct wiring lives in
``react_runtime``, provider contract handling in ``context``, and deterministic
post-processing in ``validation``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import ValidationError

from planner_agents.context import call_tool, load_memory_context, validate_memory_context, validate_todos
from planner_agents.contracts import DailyPlan, PlanDraft, PlanItem, PlanValidation, TextAgent
from planner_agents.errors import (
    PlanValidationError,
    PlannerConfigurationError,
    PlannerError,
    PlannerOutputError,
    PlannerToolError,
)
from planner_agents.prompts import (
    build_context_prompt,
    build_react_prompt,
    build_revision_prompt,
    goal_needs_weather,
    parse_draft,
)
from planner_agents.react_runtime import ReActPlannerRuntime
from planner_agents.validation import apply_memory_context, validate_draft
from tools.calendar_tool import get_calendar_events
from tools.memory_context import get_memory_context
from tools.todo_tool import get_todos
from tools.weather_tool import get_weather


MAX_REACT_TURNS = 6


class PlannerAgent:
    """Coordinate planning inputs without owning provider or SDK implementation."""

    def __init__(
        self,
        text_agent: TextAgent | None = None,
        *,
        sdk_model: Any | None = None,
        calendar_getter: Callable[..., Any] = get_calendar_events,
        todo_getter: Callable[..., Any] = get_todos,
        weather_getter: Callable[..., Any] = get_weather,
        memory_getter: Callable[..., Any] = get_memory_context,
        preference_getter: Callable[..., Any] | None = None,
        max_revision_attempts: int = 1,
        max_react_turns: int = MAX_REACT_TURNS,
    ) -> None:
        if max_revision_attempts < 0:
            raise ValueError("max_revision_attempts must be non-negative")
        if max_react_turns < 1:
            raise ValueError("max_react_turns must be at least 1")
        self.text_agent = text_agent
        self.sdk_model = sdk_model
        self.calendar_getter = calendar_getter
        self.todo_getter = todo_getter
        self.weather_getter = weather_getter
        self.memory_getter = memory_getter
        self.preference_getter = preference_getter
        self.max_revision_attempts = max_revision_attempts
        self.max_react_turns = max_react_turns
        self._react_runtime = ReActPlannerRuntime(
            sdk_model=sdk_model,
            calendar_getter=calendar_getter,
            todo_getter=todo_getter,
            weather_getter=weather_getter,
            memory_loader=self._load_memory_context,
        )

    async def create_daily_plan(
        self,
        goal: str,
        *,
        target_date: str | date | None = None,
        location: str | None = None,
        user_id: str = "default",
    ) -> DailyPlan:
        """Create a validated plan through the SDK or deterministic offline path."""

        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must not be blank")
        if location is not None and (not isinstance(location, str) or not location.strip()):
            raise ValueError("location must not be blank when provided")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must not be blank")

        clean_goal = goal.strip()
        clean_location = location.strip() if location else None
        kwargs = {
            "plan_date": _normalise_date(target_date or date.today()),
            "location": clean_location,
            "user_id": user_id.strip(),
        }
        if self.text_agent is None:
            return await self._create_daily_plan_with_sdk(clean_goal, **kwargs)
        return await self._create_daily_plan_with_text_agent(clean_goal, **kwargs)

    async def _create_daily_plan_with_text_agent(
        self, clean_goal: str, *, plan_date: str, location: str | None, user_id: str
    ) -> DailyPlan:
        """Offline fallback retaining deterministic test-double support."""

        calendar_events = await self._call_tool("calendar", self.calendar_getter, plan_date)
        todos = self._validate_todos(await self._call_tool("todo", self.todo_getter))
        memory_context = await self._load_memory_context(user_id, clean_goal)
        tools_used = ["calendar", "todo", "memory"]
        weather_context: dict[str, Any] | None = None
        routing_assumptions: list[str] = []
        if self._goal_needs_weather(clean_goal):
            if location:
                weather_context = await self._call_tool("weather", self.weather_getter, location, plan_date)
                tools_used.append("weather")
            else:
                routing_assumptions.append("Weather was not checked because no location was provided.")

        initial_prompt = self._build_prompt(
            goal=clean_goal, target_date=plan_date, calendar_events=calendar_events,
            todos=todos, memory_context=memory_context, weather=weather_context,
        )
        conflicts: list[str] = []
        for attempt in range(self.max_revision_attempts + 1):
            draft = await self._generate_draft(
                initial_prompt if not attempt else self._build_revision_prompt(conflicts)
            )
            plan, validation = self._validate_and_build_plan(
                draft=draft, goal=clean_goal, plan_date=plan_date,
                calendar_events=calendar_events, todos=todos,
                memory_context=memory_context, weather=weather_context,
                tools_used=tools_used, routing_assumptions=routing_assumptions,
            )
            if plan is not None:
                return plan
            conflicts = validation.conflicts
        raise PlanValidationError(_validation_failure(conflicts))

    async def _create_daily_plan_with_sdk(
        self, clean_goal: str, *, plan_date: str, location: str | None, user_id: str
    ) -> DailyPlan:
        """Use the SDK runtime for a bounded ReAct loop, then validate locally."""

        routing_assumptions = (
            ["Weather was not checked because no location was provided."]
            if self._goal_needs_weather(clean_goal) and not location else []
        )
        sdk_agent, tool_state, _ = self._build_sdk_agent(
            clean_goal, plan_date=plan_date, location=location, user_id=user_id
        )
        initial_prompt = self._build_react_prompt(
            goal=clean_goal, target_date=plan_date, location=location
        )
        conflicts: list[str] = []
        for attempt in range(self.max_revision_attempts + 1):
            prompt = initial_prompt if not attempt else (
                f"{initial_prompt}\n\nThe previous plan had these deterministic conflicts. "
                "Use the available read-only tools as needed, then return only a corrected complete JSON object:\n"
                f"{json.dumps(conflicts, ensure_ascii=False)}"
            )
            draft = await self._generate_draft_with_sdk(
                sdk_agent, prompt, max_turns=self.max_react_turns
            )
            calendar_events = tool_state.get("calendar_events")
            memory_context = tool_state.get("memory_context")
            if calendar_events is None or memory_context is None:
                raise PlannerToolError("ReAct planner must read calendar and memory before finalizing.")
            plan, validation = self._validate_and_build_plan(
                draft=draft, goal=clean_goal, plan_date=plan_date,
                calendar_events=calendar_events, todos=tool_state.get("todos", []),
                memory_context=memory_context, weather=tool_state.get("weather"),
                tools_used=list(tool_state["tools_used"]), routing_assumptions=routing_assumptions,
            )
            if plan is not None:
                return plan
            conflicts = validation.conflicts
        raise PlanValidationError(_validation_failure(conflicts))

    def _validate_and_build_plan(
        self,
        *,
        draft: PlanDraft,
        goal: str,
        plan_date: str,
        calendar_events: object,
        todos: list[dict[str, Any]],
        memory_context: dict[str, Any],
        weather: dict[str, Any] | None,
        tools_used: list[str],
        routing_assumptions: list[str],
    ) -> tuple[DailyPlan | None, PlanValidation]:
        adjusted_draft, memory_assumptions = self._apply_memory_context(
            draft, calendar_events, memory_context, goal
        )
        validation = self._validate_draft(adjusted_draft, calendar_events)
        if not validation.is_valid:
            return None, validation
        return DailyPlan(
            date=plan_date, goal=goal, summary=adjusted_draft.summary,
            schedule=adjusted_draft.schedule,
            assumptions=adjusted_draft.assumptions + routing_assumptions + memory_assumptions,
            tools_used=tools_used, calendar_events_considered=len(calendar_events),
            todo_items_considered=len(todos),
            memory_preferences_considered=len(memory_context["preferences"]),
            weather=weather, validation=validation,
        ), validation

    async def _load_memory_context(self, user_id: str, goal: str) -> dict[str, Any]:
        return await load_memory_context(
            user_id=user_id, goal=goal, memory_getter=self.memory_getter,
            preference_getter=self.preference_getter,
        )

    async def _generate_draft(self, prompt: str) -> PlanDraft:
        structured_runner = getattr(self.text_agent, "run_structured", None)
        if callable(structured_runner):
            try:
                return PlanDraft.model_validate(await structured_runner(prompt, PlanDraft))
            except ValidationError as exc:
                raise PlannerOutputError(f"Kimi returned an invalid daily-plan structure: {exc}") from exc
        return self._parse_draft(await self.text_agent.run(prompt))

    async def _generate_draft_with_sdk(self, sdk_agent: Any, prompt: str, *, max_turns: int = MAX_REACT_TURNS) -> PlanDraft:
        return await self._react_runtime.generate(sdk_agent, prompt, max_turns=max_turns)

    def _build_sdk_agent(
        self, goal: str, *, plan_date: str, location: str | None, user_id: str = "default"
    ) -> tuple[Any, dict[str, Any], list[str]]:
        agent, state = self._react_runtime.build_agent(
            goal=goal, plan_date=plan_date, location=location, user_id=user_id
        )
        return agent, state, []

    # Compatibility helpers kept for current callers and tests.
    _call_tool = staticmethod(call_tool)
    _validate_todos = staticmethod(validate_todos)
    _validate_memory_context = staticmethod(validate_memory_context)
    _apply_memory_context = staticmethod(apply_memory_context)
    _validate_draft = staticmethod(validate_draft)
    _goal_needs_weather = staticmethod(goal_needs_weather)
    _build_prompt = staticmethod(build_context_prompt)
    _build_react_prompt = staticmethod(build_react_prompt)
    _build_revision_prompt = staticmethod(build_revision_prompt)
    _parse_draft = staticmethod(parse_draft)


def _normalise_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("target_date must be a non-blank ISO date")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("target_date must use YYYY-MM-DD format") from exc


def _validation_failure(conflicts: list[str]) -> str:
    return f"Kimi could not produce a conflict-free plan: {'; '.join(conflicts) or 'unknown conflict'}"


__all__ = [
    "DailyPlan", "PlanDraft", "PlanItem", "PlanValidation", "PlannerAgent",
    "PlannerConfigurationError", "PlannerError", "PlannerOutputError", "PlannerToolError",
    "PlanValidationError", "TextAgent",
]
