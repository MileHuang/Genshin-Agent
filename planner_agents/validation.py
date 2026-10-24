"""Deterministic post-processing that remains outside the LLM runtime."""

from __future__ import annotations

from typing import Any

from planner_agents.contracts import DailyPlan, PlanDraft, PlanValidation
from planner_agents.errors import PlannerToolError
from tools.memory_rules import apply_memory_rules
from tools.validator_tool import validate_schedule


def ensure_calendar_slot_available(plan: DailyPlan, start: str, end: str) -> None:
    """Check edits against the same fixed commitments used during planning."""
    if len(plan.calendar_events) != plan.calendar_events_considered:
        raise ValueError("Fixed calendar context is missing. Generate a new plan before moving items.")
    for event in plan.calendar_events:
        if start < event.end_time and event.start_time < end:
            raise ValueError(f'The new time conflicts with fixed calendar event "{event.title}".')


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
