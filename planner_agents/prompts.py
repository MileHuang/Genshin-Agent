"""Prompts and pure parsing helpers for the daily-planning domain."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from planner_agents.contracts import PlanDraft
from planner_agents.errors import PlannerOutputError


PLANNER_PROMPT = """
Create a realistic, executable daily plan from the user's goal and supplied
context.

Context rules:
- Calendar events are fixed commitments and must never be moved.
- Todo items are tasks the user wants to complete; schedule pending items when
  realistically possible.
- Memory contains an explicit profile, evidence-backed behavioral preferences,
  and goal-relevant events. Active preferences are soft constraints. Prefer
  preferred_time_range, avoid avoid_time_range, and reduce proactive priority
  for deprioritize_activity. The user's current explicit goal always wins.
- Ignore paused preferences.
- Weather only affects outdoor, travel, commute, or weather-sensitive activity.

Return exactly one JSON object with this structure:
{
  "summary": "brief planning strategy",
  "schedule": [{"title": "activity", "start_time": "HH:MM", "end_time": "HH:MM", "priority": "low|medium|high", "category": "short category", "notes": "brief useful note"}],
  "assumptions": ["material assumption"]
}

Requirements:
- Use 24-hour HH:MM times and chronological order.
- Do not overlap new items with each other or fixed calendar events.
- Schedule only new user activities; do not repeat fixed events as tasks.
- Include realistic transitions, meals, breaks, and recovery.
- Keep the workload achievable.
- Do not invent calendar, weather, todo, profile, or memory facts.
- Do not wrap the JSON in Markdown or add text outside it.
""".strip()

ENGLISH_WEATHER_KEYWORDS = {
    "travel", "trip", "outdoor", "outdoors", "outside", "weather", "walk",
    "walking", "hike", "hiking", "run", "running", "cycle", "cycling",
    "commute", "commuting", "park", "beach", "picnic", "drive", "driving",
    "flight",
}


def goal_needs_weather(goal: str) -> bool:
    return bool(set(re.findall(r"[a-z]+", goal.casefold())) & ENGLISH_WEATHER_KEYWORDS)


def build_context_prompt(
    *, goal: str, target_date: str, calendar_events: object, todos: object,
    memory_context: object, weather: object,
) -> str:
    return f"{PLANNER_PROMPT}\n\nPlanning context:\n" + json.dumps(
        {"goal": goal, "date": target_date, "calendar_events": calendar_events,
         "todos": todos, "memory_context": memory_context, "weather": weather},
        ensure_ascii=False, indent=2, default=str,
    )


def build_react_prompt(*, goal: str, target_date: str, location: str | None) -> str:
    return (
        "Plan this request using your available read-only tools.\n"
        f"Goal: {goal}\nDate: {target_date}\nLocation: {location or 'not provided'}\n"
        "Do not expose internal reasoning. Return JSON only after tool use."
    )


def build_revision_prompt(conflicts: list[str]) -> str:
    return (
        "Revise the previous JSON plan to remove these deterministic conflicts: "
        f"{json.dumps(conflicts, ensure_ascii=False)}. Return only the corrected "
        "JSON object using the same schema."
    )


def parse_draft(raw_output: str) -> PlanDraft:
    if not isinstance(raw_output, str) or not raw_output.strip():
        raise PlannerOutputError("Kimi returned an empty plan")
    decoder = json.JSONDecoder()
    parsed: object | None = None
    for index, character in enumerate(raw_output):
        if character == "{":
            try:
                parsed, _ = decoder.raw_decode(raw_output[index:])
                break
            except json.JSONDecodeError:
                continue
    if parsed is None:
        raise PlannerOutputError("Kimi did not return a valid JSON object")
    try:
        return PlanDraft.model_validate(parsed)
    except ValidationError as exc:
        raise PlannerOutputError(
            f"Kimi returned an invalid daily-plan structure: {exc}"
        ) from exc
