"""Run a small end-to-end Kimi daily-planning demo."""

import asyncio

from agents.basic_agent import BasicAgent
from agents.planner_agent import PlannerAgent


DEMO_GOAL = (
    "Plan a focused day with two study sessions, a 45-minute outdoor run, "
    "grocery shopping, meals, breaks, and one hour of free time."
)


async def main() -> None:
    async with BasicAgent() as basic_agent:
        planner = PlannerAgent(basic_agent)
        plan = await planner.create_daily_plan(
            DEMO_GOAL,
            location="Madison, WI",
        )
    print(plan.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
