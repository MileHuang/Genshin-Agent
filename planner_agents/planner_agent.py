"""Planner agent built on the OpenAI Agents SDK with deterministic validation."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Literal, Protocol

from openai import AsyncOpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from config.settings import (
    AGENT_SYSTEM_PROMPT,
    KIMI_BASE_URL,
    KIMI_MODEL,
    KIMI_TIMEOUT_SECONDS,
    MOONSHOT_API_KEY,
)
from tools.calendar_tool import get_calendar_events
from tools.preference_tool import get_user_preferences
from tools.validator_tool import validate_schedule
from tools.weather_tool import get_weather


class TextAgent(Protocol):
    """Minimal test-double interface for offline planner tests."""

    async def run(self, prompt: str) -> str: ...


class PlannerError(RuntimeError):
    """Base exception for planner failures."""


class PlannerOutputError(PlannerError):
    """Raised when the model does not return the required structure."""


class PlannerToolError(PlannerError):
    """Raised when a required planning tool fails."""


class PlanValidationError(PlannerError):
    """Raised when a generated plan still has deterministic conflicts."""


class PlanItem(BaseModel):
    """One scheduled item in a structured daily plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    start_time: str
    end_time: str
    priority: Literal["low", "medium", "high"] = "medium"
    category: str = Field(default="task", min_length=1)
    notes: str = ""

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        try:
            return datetime.strptime(value, "%H:%M").strftime("%H:%M")
        except (TypeError, ValueError) as exc:
            raise ValueError("time values must use 24-hour HH:MM format") from exc

    @model_validator(mode="after")
    def validate_interval(self) -> PlanItem:
        start = datetime.strptime(self.start_time, "%H:%M")
        end = datetime.strptime(self.end_time, "%H:%M")
        if start >= end:
            raise ValueError("end_time must be later than start_time")
        return self

    def to_validator_item(self) -> dict[str, str]:
        """Adapt the plan item to the existing deterministic validator."""

        return {
            "任务": self.title,
            "开始时间": self.start_time,
            "结束时间": self.end_time,
        }


class PlanDraft(BaseModel):
    """Exact JSON shape requested from Kimi before tool metadata is attached."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1)
    schedule: list[PlanItem] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_chronological_order(self) -> PlanDraft:
        starts = [item.start_time for item in self.schedule]
        if starts != sorted(starts):
            raise ValueError("schedule must be in chronological order")
        return self


class PlanValidation(BaseModel):
    is_valid: bool
    conflicts: list[str] = Field(default_factory=list)


class DailyPlan(BaseModel):
    """Structured daily-plan result returned to the application."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    date: date
    goal: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    schedule: list[PlanItem] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    calendar_events_considered: int = Field(ge=0)
    weather: dict[str, Any] | None = None
    validation: PlanValidation


PLANNER_PROMPT = """
Create a realistic daily plan from the user's goal and the supplied tool
context. Fixed calendar events are unavailable time and must never be moved.
Use preferences when they are relevant, and use weather only to adjust
location-sensitive or outdoor activities.

Return only one JSON object with exactly this shape:
{
  "summary": "brief description of the strategy",
  "schedule": [
    {
      "title": "actionable activity",
      "start_time": "HH:MM",
      "end_time": "HH:MM",
      "priority": "low|medium|high",
      "category": "short category",
      "notes": "brief useful detail"
    }
  ],
  "assumptions": ["material assumption"]
}

Requirements:
- Use 24-hour HH:MM times and chronological order.
- Do not overlap schedule items or supplied calendar events.
- Schedule only new user activities; do not repeat fixed calendar events.
- Include realistic transitions, meals, breaks, and recovery when relevant.
- Keep the workload achievable and prioritize the user's core goal.
- Do not invent tool results, calendar access, weather, or personal facts.
- Do not wrap the JSON in Markdown or add commentary outside it.
""".strip()


ENGLISH_WEATHER_KEYWORDS = {
    "travel",
    "trip",
    "outdoor",
    "outdoors",
    "outside",
    "weather",
    "walk",
    "walking",
    "hike",
    "hiking",
    "run",
    "running",
    "cycle",
    "cycling",
    "commute",
    "commuting",
    "park",
    "beach",
    "picnic",
    "drive",
    "driving",
    "flight",
}


CJK_WEATHER_KEYWORDS = {
    "旅行",
    "户外",
    "天气",
    "散步",
    "跑步",
    "通勤",
}


class PlannerAgent:
    """Use OpenAI Agents SDK tools plus deterministic checks for daily plans."""

    def __init__(
        self,
        text_agent: TextAgent | None = None,
        *,
        sdk_model: Any | None = None,
        calendar_getter: Callable[..., Any] = get_calendar_events,
        weather_getter: Callable[..., Any] = get_weather,
        preference_getter: Callable[..., Any] = get_user_preferences,
        max_revision_attempts: int = 1,
    ) -> None:
        if max_revision_attempts < 0:
            raise ValueError("max_revision_attempts must be non-negative")
        self.text_agent = text_agent
        self.sdk_model = sdk_model
        self.calendar_getter = calendar_getter
        self.weather_getter = weather_getter
        self.preference_getter = preference_getter
        self.max_revision_attempts = max_revision_attempts

    async def create_daily_plan(
        self,
        goal: str,
        *,
        target_date: str | date | None = None,
        location: str | None = None,
    ) -> DailyPlan:
        """Understand a goal, route tools, and return a validated daily plan."""

        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must not be blank")

        plan_date = _normalise_date(target_date or date.today())
        clean_goal = goal.strip()
        clean_location = location.strip() if isinstance(location, str) else None
        if location is not None and not clean_location:
            raise ValueError("location must not be blank when provided")

        if self.text_agent is None:
            return await self._create_daily_plan_with_sdk(
                clean_goal,
                plan_date=plan_date,
                location=clean_location,
            )

        return await self._create_daily_plan_with_text_agent(
            clean_goal,
            plan_date=plan_date,
            location=clean_location,
        )

    async def _create_daily_plan_with_text_agent(
        self,
        clean_goal: str,
        *,
        plan_date: str,
        location: str | None,
    ) -> DailyPlan:
        """Legacy-compatible path used by fast offline tests."""

        calendar_events = await self._call_tool(
            "calendar",
            self.calendar_getter,
            plan_date,
        )
        preferences = await self._call_tool(
            "preferences",
            self.preference_getter,
        )
        tools_used = ["calendar", "preferences"]

        weather_context: dict[str, Any] | None = None
        routing_assumptions: list[str] = []
        if self._goal_needs_weather(clean_goal):
            if location:
                weather_context = await self._call_tool(
                    "weather",
                    self.weather_getter,
                    location,
                    plan_date,
                )
                tools_used.append("weather")
            else:
                routing_assumptions.append(
                    "Weather was not checked because no location was provided."
                )

        initial_prompt = self._build_prompt(
            goal=clean_goal,
            target_date=plan_date,
            calendar_events=calendar_events,
            preferences=preferences,
            weather=weather_context,
        )

        conflicts: list[str] = []
        for attempt in range(self.max_revision_attempts + 1):
            prompt = initial_prompt
            if attempt:
                prompt = self._build_revision_prompt(conflicts)

            draft = await self._generate_draft(prompt)
            validation = self._validate_draft(draft, calendar_events)
            if validation.is_valid:
                return DailyPlan(
                    date=plan_date,
                    goal=clean_goal,
                    summary=draft.summary,
                    schedule=draft.schedule,
                    assumptions=draft.assumptions + routing_assumptions,
                    tools_used=tools_used,
                    calendar_events_considered=len(calendar_events),
                    weather=weather_context,
                    validation=validation,
                )
            conflicts = validation.conflicts

        conflict_text = "; ".join(conflicts) or "unknown conflict"
        raise PlanValidationError(
            f"Kimi could not produce a conflict-free plan: {conflict_text}"
        )

    async def _create_daily_plan_with_sdk(
        self,
        clean_goal: str,
        *,
        plan_date: str,
        location: str | None,
    ) -> DailyPlan:
        """Run the planner through OpenAI Agents SDK Agent/Runner/tools."""

        sdk_agent, tool_state, routing_assumptions = self._build_sdk_agent(
            clean_goal,
            plan_date=plan_date,
            location=location,
        )
        initial_prompt = self._build_sdk_prompt(
            goal=clean_goal,
            target_date=plan_date,
            location=location,
            needs_weather=self._goal_needs_weather(clean_goal),
        )

        conflicts: list[str] = []
        last_draft: PlanDraft | None = None
        for attempt in range(self.max_revision_attempts + 1):
            prompt = initial_prompt
            if attempt:
                prompt = self._build_sdk_revision_prompt(
                    goal=clean_goal,
                    target_date=plan_date,
                    previous_draft=last_draft,
                    conflicts=conflicts,
                )

            draft = await self._generate_draft_with_sdk(sdk_agent, prompt)
            last_draft = draft
            calendar_events = tool_state["calendar_events"]
            if calendar_events is None:
                calendar_events = await self._call_tool(
                    "calendar",
                    self.calendar_getter,
                    plan_date,
                )
                tool_state["calendar_events"] = calendar_events

            validation = self._validate_draft(draft, calendar_events)
            if validation.is_valid:
                tools_used = [
                    name
                    for name in ("calendar", "preferences", "weather")
                    if tool_state[f"{name}_used"]
                ]
                weather_context = tool_state["weather"]
                if weather_context is None and "weather" not in tools_used:
                    weather_context = None
                return DailyPlan(
                    date=plan_date,
                    goal=clean_goal,
                    summary=draft.summary,
                    schedule=draft.schedule,
                    assumptions=draft.assumptions + routing_assumptions,
                    tools_used=tools_used,
                    calendar_events_considered=len(calendar_events),
                    weather=weather_context,
                    validation=validation,
                )
            conflicts = validation.conflicts

        conflict_text = "; ".join(conflicts) or "unknown conflict"
        raise PlanValidationError(
            f"Kimi could not produce a conflict-free plan: {conflict_text}"
        )

    async def _generate_draft(self, prompt: str) -> PlanDraft:
        """Prefer SDK structured output and retain text-agent compatibility."""

        structured_runner = getattr(self.text_agent, "run_structured", None)
        if callable(structured_runner):
            result = await structured_runner(prompt, PlanDraft)
            try:
                return PlanDraft.model_validate(result)
            except ValidationError as exc:
                raise PlannerOutputError(
                    f"Kimi returned an invalid daily-plan structure: {exc}"
                ) from exc

        raw_output = await self.text_agent.run(prompt)
        return self._parse_draft(raw_output)

    async def _generate_draft_with_sdk(
        self,
        sdk_agent: Any,
        prompt: str,
    ) -> PlanDraft:
        """Run the OpenAI Agents SDK agent and normalize its structured output."""

        try:
            from agents import Runner
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI Agents SDK is not installed. Install dependencies with "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        result = await Runner.run(sdk_agent, prompt)
        output = result.final_output
        if isinstance(output, PlanDraft):
            return output
        if isinstance(output, BaseModel):
            output = output.model_dump()
        try:
            return PlanDraft.model_validate(output)
        except ValidationError as exc:
            raise PlannerOutputError(
                f"Agents SDK returned an invalid daily-plan structure: {exc}"
            ) from exc

    def _build_sdk_agent(
        self,
        goal: str,
        *,
        plan_date: str,
        location: str | None,
    ) -> tuple[Any, dict[str, Any], list[str]]:
        try:
            from agents import (
                Agent,
                OpenAIChatCompletionsModel,
                function_tool,
                set_tracing_disabled,
            )
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI Agents SDK is not installed. Install dependencies with "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        set_tracing_disabled(disabled=True)
        tool_state: dict[str, Any] = {
            "calendar_events": None,
            "preferences": None,
            "weather": None,
            "calendar_used": False,
            "preferences_used": False,
            "weather_used": False,
        }

        @function_tool
        async def get_calendar_context() -> list[dict[str, Any]]:
            """Return fixed calendar events for the requested planning date."""

            tool_state["calendar_used"] = True
            events = await self._call_tool(
                "calendar",
                self.calendar_getter,
                plan_date,
            )
            tool_state["calendar_events"] = events
            return events

        @function_tool
        async def get_preferences_context() -> dict[str, Any]:
            """Return user planning preferences such as routine and focus time."""

            tool_state["preferences_used"] = True
            preferences = await self._call_tool(
                "preferences",
                self.preference_getter,
            )
            tool_state["preferences"] = preferences
            return preferences

        tools = [get_calendar_context, get_preferences_context]
        routing_assumptions: list[str] = []
        if self._goal_needs_weather(goal):
            if location:

                @function_tool
                async def get_weather_context() -> dict[str, Any]:
                    """Return the weather forecast for the requested location/date."""

                    tool_state["weather_used"] = True
                    weather = await self._call_tool(
                        "weather",
                        self.weather_getter,
                        location,
                        plan_date,
                    )
                    tool_state["weather"] = weather
                    return weather

                tools.append(get_weather_context)
            else:
                routing_assumptions.append(
                    "Weather was not checked because no location was provided."
                )

        model = self.sdk_model
        if model is None:
            if not MOONSHOT_API_KEY:
                raise ValueError(
                    "Missing MOONSHOT_API_KEY. Add it to .env or the process "
                    "environment."
                )
            client = AsyncOpenAI(
                api_key=MOONSHOT_API_KEY,
                base_url=KIMI_BASE_URL,
                timeout=KIMI_TIMEOUT_SECONDS,
            )
            model = OpenAIChatCompletionsModel(
                model=KIMI_MODEL,
                openai_client=client,
            )

        instructions = (
            f"{AGENT_SYSTEM_PROMPT}\n\n{PLANNER_PROMPT}\n\n"
            "Use the provided function tools for calendar and preference context "
            "before producing a final answer. Use the weather tool when it is "
            "available and the goal includes outdoor, travel, commute, or "
            "weather-sensitive activity. The final answer must match the "
            "structured output schema."
        )
        sdk_agent = Agent(
            name="Personal Planner Agent",
            instructions=instructions,
            model=model,
            tools=tools,
            output_type=PlanDraft,
        )
        return sdk_agent, tool_state, routing_assumptions

    @staticmethod
    def _goal_needs_weather(goal: str) -> bool:
        lowered_goal = goal.casefold()
        english_words = set(re.findall(r"[a-z]+", lowered_goal))
        return bool(english_words & ENGLISH_WEATHER_KEYWORDS) or any(
            keyword in goal for keyword in CJK_WEATHER_KEYWORDS
        )

    @staticmethod
    async def _call_tool(
        name: str,
        tool: Callable[..., Any],
        *args: Any,
    ) -> Any:
        try:
            result = tool(*args)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:
            raise PlannerToolError(f"{name} tool failed: {exc}") from exc

    @staticmethod
    def _build_prompt(
        *,
        goal: str,
        target_date: str,
        calendar_events: object,
        preferences: object,
        weather: object,
    ) -> str:
        context = {
            "goal": goal,
            "date": target_date,
            "calendar_events": calendar_events,
            "user_preferences": preferences,
            "weather": weather,
        }
        return (
            f"{PLANNER_PROMPT}\n\nPlanning context:\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2, default=str)}"
        )

    @staticmethod
    def _build_sdk_prompt(
        *,
        goal: str,
        target_date: str,
        location: str | None,
        needs_weather: bool,
    ) -> str:
        weather_instruction = (
            "Call the weather tool before drafting because this goal is "
            "weather-sensitive."
            if needs_weather and location
            else "No weather tool is available for this request."
            if needs_weather
            else "Weather is not needed for this request."
        )
        return (
            f"Create a daily plan for {target_date}.\n"
            f"Goal: {goal}\n"
            f"Location: {location or 'not provided'}\n"
            "Call the calendar and preference tools before drafting. "
            f"{weather_instruction}"
        )

    @staticmethod
    def _build_revision_prompt(conflicts: list[str]) -> str:
        return (
            "Revise your previous JSON plan to remove these deterministic "
            f"conflicts: {json.dumps(conflicts, ensure_ascii=False)}. "
            "Return only the corrected JSON object using exactly the same schema."
        )

    @staticmethod
    def _build_sdk_revision_prompt(
        *,
        goal: str,
        target_date: str,
        previous_draft: PlanDraft | None,
        conflicts: list[str],
    ) -> str:
        previous = (
            previous_draft.model_dump(mode="json") if previous_draft else None
        )
        return (
            f"Revise the daily plan for {target_date}.\n"
            f"Goal: {goal}\n"
            "The previous draft had deterministic conflicts:\n"
            f"{json.dumps(conflicts, ensure_ascii=False)}\n"
            "Previous draft:\n"
            f"{json.dumps(previous, ensure_ascii=False, indent=2)}\n"
            "Call calendar and preference tools again if needed, then return a "
            "corrected structured plan with no overlaps."
        )

    @staticmethod
    def _parse_draft(raw_output: str) -> PlanDraft:
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise PlannerOutputError("Kimi returned an empty planner response")

        parsed: object | None = None
        decoder = json.JSONDecoder()
        for index, character in enumerate(raw_output):
            if character != "{":
                continue
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

    @staticmethod
    def _validate_draft(
        draft: PlanDraft,
        calendar_events: object,
    ) -> PlanValidation:
        if not isinstance(calendar_events, list):
            raise PlannerToolError("calendar tool must return a list of events")

        schedule = [item.to_validator_item() for item in draft.schedule]
        for event in calendar_events:
            if not isinstance(event, dict):
                raise PlannerToolError("calendar events must be dictionaries")
            try:
                schedule.append(
                    {
                        "任务": str(event["title"]),
                        "开始时间": str(event["start_time"]),
                        "结束时间": str(event["end_time"]),
                    }
                )
            except KeyError as exc:
                raise PlannerToolError(
                    f"calendar event is missing required field: {exc.args[0]}"
                ) from exc

        result = validate_schedule(schedule)
        return PlanValidation(
            is_valid=bool(result["是否有效"]),
            conflicts=list(result["冲突"]),
        )


def _normalise_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("target_date must be a non-blank ISO date")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("target_date must use YYYY-MM-DD format") from exc
