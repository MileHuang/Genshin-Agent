"""Daily planner built on the OpenAI Agents SDK with deterministic checks."""

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
from tools.memory_context import get_memory_context
from tools.memory_rules import apply_memory_rules
from tools.todo_tool import get_todos
from tools.validator_tool import validate_schedule
from tools.weather_tool import get_weather


class TextAgent(Protocol):
    """Minimal text-agent interface used by offline tests."""

    async def run(self, prompt: str) -> str: ...


class PlannerError(RuntimeError):
    """Base exception for planner failures."""


class PlannerOutputError(PlannerError):
    """Raised when the model does not return the required structure."""


class PlannerToolError(PlannerError):
    """Raised when a planning-context provider fails."""


class PlannerConfigurationError(PlannerError):
    """Required planner configuration is missing or invalid."""


class PlanValidationError(PlannerError):
    """Raised when generated output still has deterministic conflicts."""


class PlanItem(BaseModel):
    """One scheduled item in a daily plan."""

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
        """Adapt the item to the deterministic schedule validator."""

        return {
            "task": self.title,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }


class PlanDraft(BaseModel):
    """Exact JSON structure required from the model."""

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
    """Structured, validated daily plan returned to the application."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    date: date
    goal: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    schedule: list[PlanItem] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    calendar_events_considered: int = Field(ge=0)
    todo_items_considered: int = Field(default=0, ge=0)
    memory_preferences_considered: int = Field(default=0, ge=0)
    weather: dict[str, Any] | None = None
    validation: PlanValidation


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
  "schedule": [
    {
      "title": "specific executable activity",
      "start_time": "HH:MM",
      "end_time": "HH:MM",
      "priority": "low|medium|high",
      "category": "short category",
      "notes": "brief useful note"
    }
  ],
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


class PlannerAgent:
    """Generate daily plans from tools, memory, and deterministic checks."""

    def __init__(
        self,
        text_agent: TextAgent | None = None,
        *,
        sdk_model: Any | None = None,
        calendar_getter: Callable[..., Any] = get_calendar_events,
        todo_getter: Callable[..., Any] = get_todos,
        weather_getter: Callable[..., Any] = get_weather,
        memory_getter: Callable[..., Any] = get_memory_context,
        preference_getter: Callable[..., Any] | None = None,
        max_revision_attempts: int = 1,
    ) -> None:
        if max_revision_attempts < 0:
            raise ValueError("max_revision_attempts must be non-negative")
        self.text_agent = text_agent
        self.sdk_model = sdk_model
        self.calendar_getter = calendar_getter
        self.todo_getter = todo_getter
        self.weather_getter = weather_getter
        self.memory_getter = memory_getter
        self.preference_getter = preference_getter
        self.max_revision_attempts = max_revision_attempts

    async def create_daily_plan(
        self,
        goal: str,
        *,
        target_date: str | date | None = None,
        location: str | None = None,
        user_id: str = "default",
    ) -> DailyPlan:
        """Read planning context and return a conflict-free daily plan."""

        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must not be blank")

        plan_date = _normalise_date(target_date or date.today())
        clean_goal = goal.strip()
        clean_location = location.strip() if isinstance(location, str) else None
        if location is not None and not clean_location:
            raise ValueError("location must not be blank when provided")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must not be blank")
        clean_user_id = user_id.strip()

        if self.text_agent is None:
            return await self._create_daily_plan_with_sdk(
                clean_goal,
                plan_date=plan_date,
                location=clean_location,
                user_id=clean_user_id,
            )

        return await self._create_daily_plan_with_text_agent(
            clean_goal,
            plan_date=plan_date,
            location=clean_location,
            user_id=clean_user_id,
        )

    async def _create_daily_plan_with_text_agent(
        self,
        clean_goal: str,
        *,
        plan_date: str,
        location: str | None,
        user_id: str,
    ) -> DailyPlan:
        """Offline path using a deterministic text-agent test double."""

        calendar_events = await self._call_tool(
            "calendar",
            self.calendar_getter,
            plan_date,
        )
        todos = self._validate_todos(
            await self._call_tool("todo", self.todo_getter)
        )
        memory_context = await self._load_memory_context(user_id, clean_goal)

        tools_used = ["calendar", "todo", "memory"]

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
            todos=todos,
            memory_context=memory_context,
            weather=weather_context,
        )

        conflicts: list[str] = []
        for attempt in range(self.max_revision_attempts + 1):
            prompt = initial_prompt
            if attempt:
                prompt = self._build_revision_prompt(conflicts)

            draft = await self._generate_draft(prompt)
            draft, memory_assumptions = self._apply_memory_context(
                draft,
                calendar_events,
                memory_context,
                clean_goal,
            )
            validation = self._validate_draft(draft, calendar_events)
            if validation.is_valid:
                return DailyPlan(
                    date=plan_date,
                    goal=clean_goal,
                    summary=draft.summary,
                    schedule=draft.schedule,
                    assumptions=(
                        draft.assumptions
                        + routing_assumptions
                        + memory_assumptions
                    ),
                    tools_used=tools_used,
                    calendar_events_considered=len(calendar_events),
                    todo_items_considered=len(todos),
                    memory_preferences_considered=len(
                        memory_context["preferences"]
                    ),
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
        user_id: str,
    ) -> DailyPlan:
        """Fetch context deterministically, then generate through the SDK."""

        calendar_events = await self._call_tool(
            "calendar", self.calendar_getter, plan_date
        )
        todos = self._validate_todos(
            await self._call_tool("todo", self.todo_getter)
        )
        memory_context = await self._load_memory_context(user_id, clean_goal)
        tools_used = ["calendar", "todo", "memory"]

        weather_context: dict[str, Any] | None = None
        routing_assumptions: list[str] = []
        if self._goal_needs_weather(clean_goal):
            if location:
                weather_context = await self._call_tool(
                    "weather", self.weather_getter, location, plan_date
                )
                tools_used.append("weather")
            else:
                routing_assumptions.append(
                    "Weather was not checked because no location was provided."
                )

        sdk_agent, _, _ = self._build_sdk_agent(
            clean_goal,
            plan_date=plan_date,
            location=location,
        )
        initial_prompt = self._build_prompt(
            goal=clean_goal,
            target_date=plan_date,
            calendar_events=calendar_events,
            todos=todos,
            memory_context=memory_context,
            weather=weather_context,
        )

        conflicts: list[str] = []
        for attempt in range(self.max_revision_attempts + 1):
            prompt = initial_prompt
            if attempt:
                prompt = (
                    f"{initial_prompt}\n\n"
                    "The previous plan had these deterministic conflicts. "
                    "Return only a corrected complete JSON object:\n"
                    f"{json.dumps(conflicts, ensure_ascii=False)}"
                )

            draft = await self._generate_draft_with_sdk(sdk_agent, prompt)
            draft, memory_assumptions = self._apply_memory_context(
                draft,
                calendar_events,
                memory_context,
                clean_goal,
            )

            validation = self._validate_draft(draft, calendar_events)
            if validation.is_valid:
                return DailyPlan(
                    date=plan_date,
                    goal=clean_goal,
                    summary=draft.summary,
                    schedule=draft.schedule,
                    assumptions=(
                        draft.assumptions
                        + routing_assumptions
                        + memory_assumptions
                    ),
                    tools_used=tools_used,
                    calendar_events_considered=len(calendar_events),
                    todo_items_considered=len(todos),
                    memory_preferences_considered=len(
                        memory_context["preferences"]
                    ),
                    weather=weather_context,
                    validation=validation,
                )

            conflicts = validation.conflicts

        conflict_text = "; ".join(conflicts) or "unknown conflict"
        raise PlanValidationError(
            f"Kimi could not produce a conflict-free plan: {conflict_text}"
        )

    async def _load_memory_context(
        self,
        user_id: str,
        goal: str,
    ) -> dict[str, Any]:
        """Load the shared memory contract or adapt a legacy preference getter."""

        if self.preference_getter is not None:
            legacy = await self._call_tool("memory", self.preference_getter)
            if not isinstance(legacy, dict):
                raise PlannerToolError("preference getter must return a dictionary")
            learned = legacy.get("learned_preferences", [])
            if not isinstance(learned, list):
                raise PlannerToolError("learned_preferences must be a list")
            profile = {
                key: value
                for key, value in legacy.items()
                if key != "learned_preferences"
            }
            context = {
                "profile": profile,
                "preferences": learned,
                "relevant_events": [],
            }
        else:
            context = await self._call_tool(
                "memory",
                self.memory_getter,
                user_id,
                goal,
            )
        return self._validate_memory_context(context)

    @staticmethod
    def _validate_memory_context(context: object) -> dict[str, Any]:
        if not isinstance(context, dict):
            raise PlannerToolError("memory context must be a dictionary")
        required = {"profile", "preferences", "relevant_events"}
        missing = required - set(context)
        if missing:
            raise PlannerToolError(
                f"memory context is missing fields: {', '.join(sorted(missing))}"
            )
        if not isinstance(context["profile"], dict):
            raise PlannerToolError("memory profile must be a dictionary")
        if not isinstance(context["preferences"], list):
            raise PlannerToolError("memory preferences must be a list")
        if not isinstance(context["relevant_events"], list):
            raise PlannerToolError("relevant_events must be a list")
        return context

    @staticmethod
    def _apply_memory_context(
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
        return (
            PlanDraft(
                summary=draft.summary,
                schedule=schedule,
                assumptions=draft.assumptions,
            ),
            explanations,
        )

    async def _generate_draft(self, prompt: str) -> PlanDraft:
        """Generate and parse a draft while retaining test-double support."""

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
        """Run the Agents SDK and validate Kimi's JSON-compatible text."""

        try:
            from agents import Runner
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI Agents SDK is not installed. Run "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        result = await Runner.run(sdk_agent, prompt)
        output = result.final_output
        if isinstance(output, PlanDraft):
            return output
        if isinstance(output, BaseModel):
            output = output.model_dump()
        if isinstance(output, str):
            return self._parse_draft(output)
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
                set_tracing_disabled,
            )
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI Agents SDK is not installed. Run "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        set_tracing_disabled(disabled=True)

        model = self.sdk_model
        if model is None:
            if not MOONSHOT_API_KEY:
                raise PlannerConfigurationError(
                    "Missing a valid MOONSHOT_API_KEY. Replace the placeholder "
                    "in .env with your Moonshot API key, or run with --demo."
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
            "The caller supplies calendar, todo, memory, and optional weather "
            "context directly in the message. Base the plan only on that context "
            "and return no text outside the required JSON object."
        )

        sdk_agent = Agent(
            name="Personal Planner Agent",
            instructions=instructions,
            model=model,
            tools=[],
        )
        return sdk_agent, {}, []

    @staticmethod
    def _goal_needs_weather(goal: str) -> bool:
        lowered_goal = goal.casefold()
        english_words = set(re.findall(r"[a-z]+", lowered_goal))
        return bool(english_words & ENGLISH_WEATHER_KEYWORDS)

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
    def _validate_todos(todos: object) -> list[dict[str, Any]]:
        if not isinstance(todos, list):
            raise PlannerToolError("todo tool must return a list of items")
        if any(not isinstance(item, dict) for item in todos):
            raise PlannerToolError("todo items must be dictionaries")
        return todos

    @staticmethod
    def _build_prompt(
        *,
        goal: str,
        target_date: str,
        calendar_events: object,
        todos: object,
        memory_context: object,
        weather: object,
    ) -> str:
        context = {
            "goal": goal,
            "date": target_date,
            "calendar_events": calendar_events,
            "todos": todos,
            "memory_context": memory_context,
            "weather": weather,
        }
        return (
            f"{PLANNER_PROMPT}\n\nPlanning context:\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2, default=str)}"
        )

    @staticmethod
    def _build_revision_prompt(conflicts: list[str]) -> str:
        return (
            "Revise the previous JSON plan to remove these deterministic "
            f"conflicts: {json.dumps(conflicts, ensure_ascii=False)}. "
            "Return only the corrected JSON object using the same schema."
        )

    @staticmethod
    def _parse_draft(raw_output: str) -> PlanDraft:
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise PlannerOutputError("Kimi returned an empty plan")

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
            raise PlannerToolError("calendar tool must return an event list")

        schedule = [item.to_validator_item() for item in draft.schedule]
        for event in calendar_events:
            if not isinstance(event, dict):
                raise PlannerToolError("calendar events must be dictionaries")
            try:
                schedule.append(
                    {
                        "task": str(event["title"]),
                        "start_time": str(event["start_time"]),
                        "end_time": str(event["end_time"]),
                    }
                )
            except KeyError as exc:
                raise PlannerToolError(
                    f"calendar event is missing field: {exc.args[0]}"
                ) from exc

        try:
            result = validate_schedule(schedule)
        except (KeyError, TypeError, ValueError) as exc:
            raise PlannerToolError(
                f"calendar tool returned an invalid event: {exc}"
            ) from exc
        return PlanValidation(
            is_valid=bool(result["is_valid"]),
            conflicts=list(result["conflicts"]),
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
