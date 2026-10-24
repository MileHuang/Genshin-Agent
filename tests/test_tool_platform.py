import asyncio

import pytest
from pydantic import BaseModel, ValidationError

from planner_agents.errors import PlannerToolError
from planner_agents.tool_platform import (
    DEFAULT_TOOL_REGISTRY,
    EmptyInput,
    TodoOutput,
    ToolExecutor,
    ToolPermission,
    ToolRegistry,
    ToolSpec,
)


def test_default_registry_exposes_typed_public_contracts():
    descriptions = DEFAULT_TOOL_REGISTRY.describe()

    assert [item["name"] for item in descriptions] == ["calendar", "todo", "memory", "weather"]
    assert all(item["permission"] == "read" for item in descriptions)
    assert all(item["timeout_seconds"] > 0 for item in descriptions)
    assert all("input_schema" in item and "output_schema" in item for item in descriptions)


def test_write_tool_cannot_be_registered_without_approval_boundary():
    with pytest.raises(ValidationError, match="explicit user approval"):
        ToolSpec(
            name="unsafe_write",
            context_label="calendar",
            description="Write without approval.",
            input_model=EmptyInput,
            output_model=TodoOutput,
            permission=ToolPermission.WRITE,
            timeout_seconds=1,
        )


def test_executor_caches_validated_results_and_records_cache_use():
    calls = []
    trace = []
    cache = {}
    executor = ToolExecutor(DEFAULT_TOOL_REGISTRY)

    def provider():
        calls.append("called")
        return [{"title": "Task"}]

    async def run():
        first = await executor.execute("todo", provider, {}, cache=cache, trace=trace)
        second = await executor.execute("todo", provider, {}, cache=cache, trace=trace)
        return first, second

    first, second = asyncio.run(run())

    assert first == second
    assert calls == ["called"]
    assert [item.cache_used for item in trace] == [False, True]


def test_executor_rejects_malformed_provider_output_instead_of_hiding_it():
    executor = ToolExecutor(DEFAULT_TOOL_REGISTRY)

    with pytest.raises(PlannerToolError, match="todo tool returned invalid output"):
        asyncio.run(executor.execute("todo", lambda: ["bad"], {}, cache={}, trace=[]))


def test_unapproved_write_execution_is_blocked():
    class Result(BaseModel):
        ok: bool

    registry = ToolRegistry([
        ToolSpec(
            name="calendar_write",
            context_label="calendar",
            description="Write a confirmed calendar draft.",
            input_model=EmptyInput,
            output_model=Result,
            permission=ToolPermission.WRITE,
            timeout_seconds=1,
            approval_required=True,
        )
    ])

    with pytest.raises(PlannerToolError, match="explicit user approval"):
        asyncio.run(ToolExecutor(registry).execute(
            "calendar_write", lambda: {"ok": True}, {}, cache={}, trace=[]
        ))
