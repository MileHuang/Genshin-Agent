"""Command-line entry point for the Personal Planner MVP."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from openai import AuthenticationError

from memory.rag import HashEmbeddingProvider, PersonalRagStore
from planner_agents.planner_agent import PlannerAgent, PlannerConfigurationError
from tools.edit_event import EditEventStore
from tools.feedback_service import FeedbackService
from tools.memory_context import RagMemoryContextService, get_memory_context
from tools.weather_tool import MockWeatherProvider, get_weather
from tools.calendar_tool import GoogleCalendarProvider, get_calendar_events
from tools.todo_tool import TodoistProvider, get_todos


DEMO_GOAL = (
    "Plan a focused day with two study sessions, a 45-minute outdoor run, "
    "grocery shopping, meals, breaks, and one hour of free time."
)
BEHAVIOR_HISTORY_PATH = Path(__file__).resolve().parent / "data" / "behavior_history.jsonl"


class DemoTextAgent:
    """Deterministic local model used to demonstrate the MVP offline."""

    async def run(self, _: str) -> str:
        return json.dumps(
            {
                "summary": "Prioritize focused work, then leave room for health and errands.",
                "schedule": [
                    {"title": "Focused study session", "start_time": "08:30", "end_time": "10:00", "priority": "high", "category": "study", "notes": "Start with the most demanding topic."},
                    {"title": "Lunch and recovery break", "start_time": "12:00", "end_time": "13:00", "priority": "medium", "category": "break", "notes": "Step away from screens."},
                    {"title": "Second study session", "start_time": "13:00", "end_time": "14:30", "priority": "high", "category": "study", "notes": "Review and consolidate notes."},
                    {"title": "Outdoor run", "start_time": "16:30", "end_time": "17:15", "priority": "medium", "category": "health", "notes": "Adjust intensity for the weather."},
                    {"title": "Grocery shopping", "start_time": "17:30", "end_time": "18:10", "priority": "medium", "category": "errand", "notes": "Buy ingredients for dinner and tomorrow."},
                    {"title": "Free time", "start_time": "20:00", "end_time": "21:00", "priority": "low", "category": "personal", "notes": "Keep this block unscheduled."},
                ],
                "assumptions": ["The day starts at 08:00."],
            },
            ensure_ascii=False,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a validated daily plan.")
    parser.add_argument("--goal", default=DEMO_GOAL, help="Natural-language planning goal.")
    parser.add_argument("--date", dest="target_date", default=date.today().isoformat(), help="Target ISO date (YYYY-MM-DD).")
    parser.add_argument("--location", help="Location for weather-aware goals.")
    parser.add_argument("--demo", action="store_true", help="Run a deterministic local demo without Moonshot.")
    parser.add_argument(
        "--google-calendar",
        action="store_true",
        help="Read fixed events from Google Calendar (first use opens a browser for OAuth).",
    )
    parser.add_argument(
        "--todoist",
        action="store_true",
        help="Read active Todoist tasks using TODOIST_API_TOKEN from .env.",
    )
    parser.add_argument(
        "--feedback",
        action="store_true",
        help="After generating a plan, interactively record accept, move, skip, or delete feedback.",
    )
    parser.add_argument(
        "--user-id",
        default="default",
        help="User whose profile and behavioral memory should be used.",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    memory_service = (
        RagMemoryContextService(
            rag_store=PersonalRagStore(embedder=HashEmbeddingProvider())
        )
        if args.demo
        else RagMemoryContextService()
    )
    memory_service.begin_session(
        args.user_id,
        f"daily-plan-{args.target_date}",
        args.goal,
    )
    calendar_getter = (
        (lambda target_date: get_calendar_events(
            target_date, provider=GoogleCalendarProvider()
        ))
        if args.google_calendar
        else get_calendar_events
    )
    todo_getter = (
        (lambda: get_todos(provider=TodoistProvider()))
        if args.todoist
        else get_todos
    )
    planner = (
        PlannerAgent(
            DemoTextAgent(),
            calendar_getter=calendar_getter,
            todo_getter=todo_getter,
            memory_getter=memory_service.get_memory_context,
            weather_getter=lambda location, target_date: get_weather(
                location,
                target_date,
                provider=MockWeatherProvider(),
            ),
        )
        if args.demo
        else PlannerAgent(
            calendar_getter=calendar_getter,
            todo_getter=todo_getter,
            memory_getter=memory_service.get_memory_context,
        )
    )
    try:
        plan = await planner.create_daily_plan(
            args.goal,
            target_date=args.target_date,
            location=args.location,
            user_id=args.user_id,
        )
    except PlannerConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except AuthenticationError:
        print(
            "Moonshot authentication failed. Check MOONSHOT_API_KEY in .env "
            "and confirm that the key is active. You can run the offline demo "
            "with: python main.py --demo",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    print(plan.model_dump_json(indent=2))
    if args.feedback:
        _collect_feedback(
            plan,
            plan_id=f"daily-plan-{plan.date.isoformat()}",
            user_id=args.user_id,
        )


def _collect_feedback(plan, *, plan_id: str, user_id: str = "default") -> None:
    """Collect plan feedback in the terminal and append it to history."""

    feedback = FeedbackService(EditEventStore(BEHAVIOR_HISTORY_PATH))
    skipped_items = []
    print("\nRecord plan feedback (enter q to finish):")

    while True:
        for index, item in enumerate(plan.schedule, start=1):
            print(f"  {index}. {item.title} ({item.start_time}-{item.end_time})")
        if skipped_items:
            print("\nSkipped today:")
            for item in skipped_items:
                print(f"  - {item.title} ({item.start_time}-{item.end_time})")

        choice = input("Select an item number, or enter q to finish: ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            break
        try:
            item = plan.schedule[int(choice) - 1]
        except (ValueError, IndexError):
            print("Enter a listed item number or q.")
            continue

        action = input("Action (accept / move / skip / delete): ").strip().lower()
        if action not in {"accept", "move", "skip", "delete"}:
            print("Invalid action. Enter accept, move, skip, or delete.")
            continue

        new_start = new_end = None
        if action == "move":
            new_range = input("New time (for example 21-22 or 21:00-22:00): ").strip()
            try:
                new_start, new_end = _parse_time_range(plan.date, new_range)
            except ValueError:
                print("Invalid time format. Use 21-22 or 21:00-22:00.")
                continue

            moves = _resolve_move(
                plan,
                item,
                new_start,
                new_end,
                user_id=user_id,
            )
            if moves is None:
                continue
            for moved_item, moved_start, moved_end in moves:
                original_start, original_end = _parse_time_range(
                    plan.date,
                    f"{moved_item.start_time}-{moved_item.end_time}",
                )
                event = feedback.move(
                    activity_type=_activity_type(moved_item.title),
                    original_start=original_start,
                    original_end=original_end,
                    new_start=moved_start,
                    new_end=moved_end,
                    source_plan_id=plan_id,
                    user_id=user_id,
                )
                moved_item.start_time = moved_start.strftime("%H:%M")
                moved_item.end_time = moved_end.strftime("%H:%M")
                print(f"Recorded {event.action.value} for {moved_item.title}.")
            plan.schedule.sort(key=lambda schedule_item: schedule_item.start_time)
            continue

        original_start, original_end = _parse_time_range(plan.date, f"{item.start_time}-{item.end_time}")
        fields = {
            "activity_type": _activity_type(item.title),
            "original_start": original_start,
            "original_end": original_end,
            "source_plan_id": plan_id,
            "user_id": user_id,
        }
        if action == "accept":
            event = feedback.accept(**fields)
        elif action == "skip":
            event = feedback.skip(**fields)
            plan.schedule.remove(item)
            skipped_items.append(item)
        else:
            event = feedback.delete(**fields)
            plan.schedule.remove(item)
        print(f"Recorded {event.action.value} for {item.title}.")

    print(f"Feedback saved to: {BEHAVIOR_HISTORY_PATH}")


def _parse_time_range(plan_date: date, value: str) -> tuple[datetime, datetime]:
    """Convert ``21-22`` or ``21:00-22:00`` to two datetimes."""

    parts = value.split("-", maxsplit=1)
    if len(parts) != 2:
        raise ValueError("time range must contain -")
    try:
        start = datetime.combine(plan_date, _parse_clock_time(parts[0])).astimezone()
        end = datetime.combine(plan_date, _parse_clock_time(parts[1])).astimezone()
    except ValueError as exc:
        raise ValueError("time values must use H or HH:MM format") from exc
    if start >= end:
        raise ValueError("end time must be later than start time")
    return start, end


def _parse_clock_time(value: str):
    """Accept whole-hour shorthand or standard HH:MM values."""

    clean_value = value.strip()
    if clean_value.isdigit():
        hour = int(clean_value)
        if 0 <= hour <= 23:
            return datetime.strptime(f"{hour:02d}:00", "%H:%M").time()
        raise ValueError("hour must be between 0 and 23")
    return datetime.strptime(clean_value, "%H:%M").time()


def _resolve_move(
    plan,
    selected_item,
    new_start: datetime,
    new_end: datetime,
    *,
    user_id: str = "default",
):
    """Return a safe move or ask for preference-guided conflict resolution."""

    conflicts = _find_conflicts(
        plan,
        new_start,
        new_end,
        excluded_items={id(selected_item)},
    )
    if not conflicts:
        return [(selected_item, new_start, new_end)]
    if len(conflicts) > 1:
        names = ", ".join(item.title for item in conflicts)
        print(
            f"The new time conflicts with multiple items: {names}. "
            "Choose another time or adjust those items individually."
        )
        return None

    conflicting_item = conflicts[0]
    learned_preferences = get_memory_context(user_id, "")["preferences"]
    selected_score = _preference_score(selected_item, new_start, new_end, learned_preferences)
    conflict_start, conflict_end = _parse_time_range(
        plan.date,
        f"{conflicting_item.start_time}-{conflicting_item.end_time}",
    )
    conflicting_score = _preference_score(
        conflicting_item,
        conflict_start,
        conflict_end,
        learned_preferences,
    )

    if selected_score >= conflicting_score:
        item_to_move = conflicting_item
        print(
            f"Conflict: {selected_item.title} overlaps {conflicting_item.title}. "
            f"Memory suggests keeping {selected_item.title} at the new time "
            f"and moving {conflicting_item.title}."
        )
    else:
        item_to_move = selected_item
        print(
            f"Conflict: {selected_item.title} overlaps {conflicting_item.title}. "
            f"Memory suggests keeping {conflicting_item.title} at its current "
            f"time and moving {selected_item.title}."
        )

    confirm = input(f"Move {item_to_move.title}? (y/n): ").strip().lower()
    if confirm not in {"y", "yes"}:
        print("Move cancelled.")
        return None

    replacement_range = input(
        f"Enter a new time for {item_to_move.title} "
        "(for example 21-22 or 21:00-22:00): "
    ).strip()
    try:
        replacement_start, replacement_end = _parse_time_range(plan.date, replacement_range)
    except ValueError:
        print("Invalid time format. Move cancelled.")
        return None

    if item_to_move is selected_item:
        return _resolve_move(
            plan,
            selected_item,
            replacement_start,
            replacement_end,
            user_id=user_id,
        )

    remaining_conflicts = _find_conflicts(
        plan,
        replacement_start,
        replacement_end,
        excluded_items={id(selected_item), id(conflicting_item)},
    )
    if _intervals_overlap(new_start, new_end, replacement_start, replacement_end):
        remaining_conflicts.append(selected_item)
    if remaining_conflicts:
        names = ", ".join(item.title for item in remaining_conflicts)
        print(
            f"The new time for {conflicting_item.title} still conflicts with "
            f"{names}. Move cancelled."
        )
        return None
    return [
        (selected_item, new_start, new_end),
        (conflicting_item, replacement_start, replacement_end),
    ]


def _find_conflicts(plan, start: datetime, end: datetime, *, excluded_items: set[int]):
    conflicts = []
    for item in plan.schedule:
        if id(item) in excluded_items:
            continue
        item_start, item_end = _parse_time_range(plan.date, f"{item.start_time}-{item.end_time}")
        if _intervals_overlap(start, end, item_start, item_end):
            conflicts.append(item)
    return conflicts


def _intervals_overlap(
    first_start: datetime,
    first_end: datetime,
    second_start: datetime,
    second_end: datetime,
) -> bool:
    return first_start < second_end and second_start < first_end


def _preference_score(item, start: datetime, end: datetime, learned_preferences: list[dict]) -> float:
    """Score how well an activity interval matches learned preferences."""

    activity_type = _activity_type(item.title)
    best_score = 0.0
    for preference in learned_preferences:
        if (
            preference.get("activity_type") != activity_type
            or preference.get("attribute") != "preferred_time_range"
        ):
            continue
        value = preference.get("value", {})
        try:
            preferred_start = _parse_clock_time(value["start"])
            preferred_end = _parse_clock_time(value["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if preferred_start <= start.time() and end.time() <= preferred_end:
            best_score = max(best_score, float(preference.get("confidence", 0)))
    return best_score


def _activity_type(title: str) -> str:
    """Create a stable activity type from a free-text title."""

    normalised = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return normalised or "other"


if __name__ == "__main__":
    asyncio.run(run())
