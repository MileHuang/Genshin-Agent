import asyncio
import json

import httpx

from agents.basic_agent import BasicAgent
from agents.planner_agent import PlannerAgent
from tools.calendar_tool import get_calendar_events
from tools.validator_tool import validate_schedule


class OfflinePlannerModel:
    """Deterministic model double; this integration test never uses network."""

    async def run(self, _: str) -> str:
        return json.dumps(
            {
                "summary": "Work around fixed calendar commitments.",
                "schedule": [
                    {
                        "title": "Focused study",
                        "start_time": "09:00",
                        "end_time": "10:00",
                        "priority": "high",
                        "category": "study",
                        "notes": "Complete the hardest topic first.",
                    },
                    {
                        "title": "Project work",
                        "start_time": "10:30",
                        "end_time": "12:00",
                        "priority": "high",
                        "category": "work",
                        "notes": "Produce one reviewable draft.",
                    },
                ],
                "assumptions": [],
            }
        )


def test_daily_planner_pipeline_produces_conflict_free_structured_plan():
    planner = PlannerAgent(OfflinePlannerModel())

    plan = asyncio.run(
        planner.create_daily_plan(
            "Study and complete project work",
            target_date="2026-08-07",
        )
    )

    assert plan.validation.is_valid is True
    assert plan.calendar_events_considered == 2
    assert [item.title for item in plan.schedule] == [
        "Focused study",
        "Project work",
    ]


def test_pipeline_validator_detects_plan_calendar_overlap():
    events = get_calendar_events("2026-08-07")
    combined_schedule = [
        {"任务": "Overlapping task", "开始时间": "10:15", "结束时间": "11:00"},
        *[
            {
                "任务": event["title"],
                "开始时间": event["start_time"],
                "结束时间": event["end_time"],
            }
            for event in events
        ],
    ]

    result = validate_schedule(combined_schedule)

    assert result["是否有效"] is False
    assert any("Team check-in" in conflict for conflict in result["冲突"])


def test_basic_agent_and_planner_integrate_without_live_network():
    model_output = json.dumps(
        {
            "summary": "Protect fixed events and complete focused work.",
            "schedule": [
                {
                    "title": "Focused study",
                    "start_time": "09:00",
                    "end_time": "10:00",
                    "priority": "high",
                    "category": "study",
                    "notes": "Finish one measurable outcome.",
                }
            ],
            "assumptions": [],
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        assert request_payload["model"] == "kimi-k3"
        assert request_payload["stream"] is False
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "mock reasoning",
                            "content": model_output,
                        }
                    }
                ]
            },
        )

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        basic_agent = BasicAgent(api_key="test-key", http_client=client)
        try:
            planner = PlannerAgent(basic_agent)
            return await planner.create_daily_plan(
                "Study efficiently",
                target_date="2026-08-07",
            )
        finally:
            await client.aclose()

    plan = asyncio.run(scenario())

    assert plan.validation.is_valid is True
    assert plan.schedule[0].title == "Focused study"
