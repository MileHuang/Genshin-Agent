import asyncio
import json
from datetime import datetime, timezone

from planner_agents.planner_agent import PlannerAgent
from tools.edit_event import EditEventStore
from tools.feedback_service import FeedbackService
from tools.memory_context import LocalMemoryContextService, MemoryControlStore


class CapturingTextAgent:
    """Offline planner double that records the supplied context."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(
            {
                "summary": "Schedule gym at the learned preferred time.",
                "schedule": [
                    {
                        "title": "Gym",
                        "start_time": "18:00",
                        "end_time": "19:00",
                        "priority": "medium",
                        "category": "health",
                        "notes": "Use learned preference.",
                    }
                ],
                "assumptions": [],
            }
        )


def _time(day: int, hour: int) -> datetime:
    return datetime(2026, 8, day, hour, 0, tzinfo=timezone.utc)


def test_feedback_to_preference_to_planner_prompt_pipeline(tmp_path):
    store = EditEventStore(tmp_path / "behavior_history.jsonl")
    feedback = FeedbackService(store)

    for day in (10, 12, 15):
        feedback.move(
            activity_type="gym",
            original_start=_time(day, 19),
            original_end=_time(day, 20),
            new_start=_time(day, 20),
            new_end=_time(day, 21),
            source_plan_id=f"plan-2026-08-{day:02d}",
            user_id="mike",
        )

    text_agent = CapturingTextAgent()
    memory_service = LocalMemoryContextService(
        event_store=store,
        control_store=MemoryControlStore(tmp_path / "memory_controls.json"),
    )
    planner = PlannerAgent(
        text_agent,
        calendar_getter=lambda _: [],
        todo_getter=lambda: [],
        memory_getter=memory_service.get_memory_context,
    )

    plan = asyncio.run(
        planner.create_daily_plan(
            "Plan a gym session",
            target_date="2026-08-18",
            user_id="mike",
        )
    )

    assert plan.validation.is_valid is True
    assert plan.schedule[0].start_time == "20:00"
    assert plan.memory_preferences_considered == 1
    assert any("Applied memory" in item for item in plan.assumptions)
    assert len(text_agent.prompts) == 1
    assert '"activity_type": "gym"' in text_agent.prompts[0]
    assert '"attribute": "preferred_time_range"' in text_agent.prompts[0]
    assert '"start": "20:00"' in text_agent.prompts[0]
