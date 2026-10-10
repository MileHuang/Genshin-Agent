"""Typed, observable execution boundary for planner tools.

The public trace records what happened, not model reasoning or provider payloads.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from datetime import date, datetime
from enum import Enum
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, model_validator

from planner_agents.errors import PlannerToolError


class ToolPermission(str, Enum):
    READ = "read"
    DRAFT = "draft"
    WRITE = "write"


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_date: date


class WeatherInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: str = Field(min_length=1)
    target_date: date


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    goal: str


class CalendarEventOutput(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)
    title: str = Field(min_length=1)
    start_time: str
    end_time: str

    @model_validator(mode="after")
    def validate_slot(self) -> "CalendarEventOutput":
        try:
            start = datetime.strptime(self.start_time, "%H:%M")
            end = datetime.strptime(self.end_time, "%H:%M")
        except (TypeError, ValueError) as exc:
            raise ValueError("event times must use 24-hour HH:MM format") from exc
        if start >= end:
            raise ValueError("calendar event end_time must be later than start_time")
        return self


class CalendarOutput(RootModel[list[CalendarEventOutput]]):
    pass


class TodoItemOutput(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)
    title: str | None = None
    content: str | None = None

    @model_validator(mode="after")
    def require_title(self) -> "TodoItemOutput":
        if not (self.title or self.content):
            raise ValueError("todo item requires title or content")
        return self


class TodoOutput(RootModel[list[TodoItemOutput]]):
    pass


class MemoryOutput(BaseModel):
    model_config = ConfigDict(extra="allow")
    profile: dict[str, Any]
    preferences: list[Any]
    relevant_events: list[Any]


class WeatherOutput(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)
    available: bool = True
    location: str | None = None
    date: str | None = None
    condition: str | None = None
    source: str | None = None
    message: str | None = None

    @model_validator(mode="after")
    def require_forecast_facts(self) -> "WeatherOutput":
        if self.available and not all((self.location, self.date, self.condition, self.source)):
            raise ValueError("available weather requires location, date, condition, and source")
        return self


FallbackFactory = Callable[[BaseModel, Exception], Any]


class ToolSpec(BaseModel):
    """Static metadata and contracts for one callable tool."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(min_length=1)
    context_label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    permission: ToolPermission
    timeout_seconds: float = Field(gt=0)
    approval_required: bool = False
    fallback_factory: FallbackFactory | None = None

    @model_validator(mode="after")
    def protect_writes(self) -> "ToolSpec":
        if self.permission is ToolPermission.WRITE and not self.approval_required:
            raise ValueError("write tools must require explicit user approval")
        return self

    def public_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "timeout_seconds": self.timeout_seconds,
            "approval_required": self.approval_required,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
        }


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def describe(self) -> list[dict[str, Any]]:
        return [spec.public_schema() for spec in self._specs.values()]


class ToolExecutionTrace(BaseModel):
    """Compact user-visible trace. It intentionally excludes arguments/results."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_name: str
    context_label: str
    description: str
    permission: ToolPermission
    status: str
    cache_used: bool = False
    fallback_applied: bool = False
    duration_ms: int = Field(ge=0)
    message: str


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(
        self,
        name: str,
        provider: Callable[..., Any],
        arguments: Mapping[str, Any],
        *,
        cache: dict[str, Any],
        trace: list[ToolExecutionTrace],
        approved: bool = False,
    ) -> Any:
        spec = self.registry.get(name)
        if spec.approval_required and not approved:
            raise PlannerToolError(f"{name} requires explicit user approval")
        try:
            parsed_input = spec.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            raise PlannerToolError(f"{name} tool received invalid input: {exc}") from exc

        cache_key = self._cache_key(name, parsed_input)
        if cache_key in cache:
            trace.append(self._trace(spec, "success", True, False, 0, "Cached result reused."))
            return cache[cache_key]

        started = monotonic()
        try:
            result = await asyncio.wait_for(
                self._invoke(provider, parsed_input), timeout=spec.timeout_seconds
            )
        except Exception as exc:
            if spec.fallback_factory is None:
                trace.append(self._trace(spec, "error", False, False, started, "Tool failed; no safe fallback was used."))
                if isinstance(exc, PlannerToolError):
                    raise
                detail = "invalid event/output" if name == "calendar" else "invalid output"
                raise PlannerToolError(f"{name} tool failed or returned {detail}: {exc}") from exc
            try:
                fallback = spec.fallback_factory(parsed_input, exc)
                validated = spec.output_model.model_validate(fallback).model_dump(mode="json")
            except Exception as fallback_exc:
                raise PlannerToolError(f"{name} fallback returned invalid output: {fallback_exc}") from fallback_exc
            trace.append(self._trace(spec, "fallback", False, True, started, "Provider unavailable; safe fallback used."))
        else:
            try:
                validated = spec.output_model.model_validate(result).model_dump(mode="json")
            except ValidationError as exc:
                trace.append(self._trace(spec, "error", False, False, started, "Tool returned malformed data; it was rejected."))
                detail = "invalid event/output" if name == "calendar" else "invalid output"
                raise PlannerToolError(f"{name} tool returned {detail}: {exc}") from exc
            trace.append(self._trace(spec, "success", False, False, started, "Tool completed successfully."))

        cache[cache_key] = validated
        return validated

    @staticmethod
    async def _invoke(provider: Callable[..., Any], parsed_input: BaseModel) -> Any:
        values = list(parsed_input.model_dump(mode="json").values())
        if inspect.iscoroutinefunction(provider):
            return await provider(*values)
        result = await asyncio.to_thread(provider, *values)
        return await result if inspect.isawaitable(result) else result

    @staticmethod
    def _cache_key(name: str, parsed_input: BaseModel) -> str:
        return f"{name}:{parsed_input.model_dump_json()}"

    @staticmethod
    def _trace(
        spec: ToolSpec,
        status: str,
        cache_used: bool,
        fallback_applied: bool,
        started: float | int,
        message: str,
    ) -> ToolExecutionTrace:
        duration = 0 if not started else max(0, round((monotonic() - started) * 1000))
        return ToolExecutionTrace(
            tool_name=spec.name,
            context_label=spec.context_label,
            description=spec.description,
            permission=spec.permission,
            status=status,
            cache_used=cache_used,
            fallback_applied=fallback_applied,
            duration_ms=duration,
            message=message,
        )


def _todo_fallback(_: BaseModel, __: Exception) -> list[Any]:
    return []


def _memory_fallback(_: BaseModel, __: Exception) -> dict[str, Any]:
    return {"profile": {}, "preferences": [], "relevant_events": [], "fallback": True}


def _weather_fallback(data: BaseModel, _: Exception) -> dict[str, Any]:
    values = data.model_dump(mode="json")
    return {
        "available": False,
        "location": values.get("location"),
        "date": values.get("target_date"),
        "source": "fallback",
        "message": "Weather data is unavailable; do not infer weather conditions.",
    }


DEFAULT_TOOL_REGISTRY = ToolRegistry(
    [
        ToolSpec(
            name="calendar",
            context_label="calendar",
            description="Read fixed calendar commitments for the planning date.",
            input_model=DateInput,
            output_model=CalendarOutput,
            permission=ToolPermission.READ,
            timeout_seconds=10,
        ),
        ToolSpec(
            name="todo",
            context_label="todo",
            description="Read open tasks that may be scheduled.",
            input_model=EmptyInput,
            output_model=TodoOutput,
            permission=ToolPermission.READ,
            timeout_seconds=10,
            fallback_factory=_todo_fallback,
        ),
        ToolSpec(
            name="memory",
            context_label="memory",
            description="Read user-scoped profile, preferences, and relevant evidence.",
            input_model=MemoryInput,
            output_model=MemoryOutput,
            permission=ToolPermission.READ,
            timeout_seconds=10,
            fallback_factory=_memory_fallback,
        ),
        ToolSpec(
            name="weather",
            context_label="weather",
            description="Read a dated forecast when weather can affect the plan.",
            input_model=WeatherInput,
            output_model=WeatherOutput,
            permission=ToolPermission.READ,
            timeout_seconds=15,
            fallback_factory=_weather_fallback,
        ),
    ]
)

