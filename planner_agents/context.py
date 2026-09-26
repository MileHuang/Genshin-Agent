"""Validation and loading helpers for read-only planner context providers."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from planner_agents.errors import PlannerToolError


async def call_tool(name: str, tool: Callable[..., Any], *args: Any) -> Any:
    """Run a sync or async provider behind one consistent error boundary."""

    try:
        result = tool(*args)
        return await result if inspect.isawaitable(result) else result
    except Exception as exc:
        raise PlannerToolError(f"{name} tool failed: {exc}") from exc


def validate_todos(todos: object) -> list[dict[str, Any]]:
    if not isinstance(todos, list) or any(not isinstance(item, dict) for item in todos):
        raise PlannerToolError("todo tool must return a list of dictionaries")
    return todos


def validate_memory_context(context: object) -> dict[str, Any]:
    if not isinstance(context, dict):
        raise PlannerToolError("memory context must be a dictionary")
    required = {"profile", "preferences", "relevant_events"}
    missing = required - set(context)
    if missing:
        raise PlannerToolError(
            f"memory context is missing fields: {', '.join(sorted(missing))}"
        )
    if not isinstance(context["profile"], dict):
        raise PlannerToolError("memory profile must be a dictionary")
    if not isinstance(context["preferences"], list):
        raise PlannerToolError("memory preferences must be a list")
    if not isinstance(context["relevant_events"], list):
        raise PlannerToolError("relevant_events must be a list")
    return context


async def load_memory_context(
    *,
    user_id: str,
    goal: str,
    memory_getter: Callable[..., Any],
    preference_getter: Callable[..., Any] | None,
) -> dict[str, Any]:
    """Read the shared memory contract or adapt the legacy preference adapter."""

    if preference_getter is not None:
        legacy = await call_tool("memory", preference_getter)
        if not isinstance(legacy, dict):
            raise PlannerToolError("preference getter must return a dictionary")
        learned = legacy.get("learned_preferences", [])
        if not isinstance(learned, list):
            raise PlannerToolError("learned_preferences must be a list")
        context: object = {
            "profile": {key: value for key, value in legacy.items() if key != "learned_preferences"},
            "preferences": learned,
            "relevant_events": [],
        }
    else:
        context = await call_tool("memory", memory_getter, user_id, goal)
    return validate_memory_context(context)
