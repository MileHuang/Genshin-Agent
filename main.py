"""Command-line entry point for the Personal Planner MVP."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

from planner_agents.planner_agent import PlannerAgent
from tools.weather_tool import MockWeatherProvider, get_weather


DEMO_GOAL = (
    "Plan a focused day with two study sessions, a 45-minute outdoor run, "
    "grocery shopping, meals, breaks, and one hour of free time."
)


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
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    planner = (
        PlannerAgent(
            DemoTextAgent(),
            weather_getter=lambda location, target_date: get_weather(
                location,
                target_date,
                provider=MockWeatherProvider(),
            ),
        )
        if args.demo
        else PlannerAgent()
    )
    plan = await planner.create_daily_plan(args.goal, target_date=args.target_date, location=args.location)
    print(plan.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(run())
