"""Deterministic post-processing that remains outside the LLM runtime."""

from __future__ import annotations

from typing import Any

from planner_agents.contracts import PlanDraft, PlanValidation
from planner_agents.errors import PlannerToolError
from tools.memory_rules import apply_memory_rules
from tools.validator_tool import validate_schedule


def apply_memory_context(
    draft: PlanDraft,
    calendar_events: object,
    memory_context: dict[str, Any],
    goal: str,
) -> tuple[PlanDraft, list[str]]:
    if not isinstance(calendar_events, list):
        raise PlannerToolError("calendar tool must return an event list")
    schedule, explanations = apply_memory_rules(
        [item.model_dump() for item in draft.schedule],
        calendar_events,
        memory_context,
        goal=goal,
    )
    return PlanDraft(
        summary=draft.summary,
        schedule=schedule,
        assumptions=draft.assumptions,
    ), explanations


def validate_draft(draft: PlanDraft, calendar_events: object) -> PlanValidation:
    if not isinstance(calendar_events, list):
        raise PlannerToolError("calendar tool must return an event list")
    schedule = [item.to_validator_item() for item in draft.schedule]
    for event in calendar_events:
        if not isinstance(event, dict):
            raise PlannerToolError("calendar events must be dictionaries")
        try:
            schedule.append({
                "task": str(event["title"]),
                "start_time": str(event["start_time"]),
                "end_time": str(event["end_time"]),
            })
        except KeyError as exc:
            raise PlannerToolError(
                f"calendar event is missing field: {exc.args[0]}"
            ) from exc
    try:
        result = validate_schedule(schedule)
    except (KeyError, TypeError, ValueError) as exc:
        raise PlannerToolError(f"calendar tool returned an invalid event: {exc}") from exc
    return PlanValidation(is_valid=bool(result["is_valid"]), conflicts=list(result["conflicts"]))
