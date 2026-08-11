"""基于 OpenAI Agents SDK 的每日计划 Agent，并使用确定性规则做冲突校验。"""

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
from tools.todo_tool import get_todos
from tools.validator_tool import validate_schedule
from tools.weather_tool import get_weather


class TextAgent(Protocol):
    """离线测试用的最小文本 Agent 接口。"""

    async def run(self, prompt: str) -> str: ...


class PlannerError(RuntimeError):
    """计划生成流程的基础异常。"""


class PlannerOutputError(PlannerError):
    """模型没有返回符合要求的结构时抛出。"""


class PlannerToolError(PlannerError):
    """计划工具调用失败时抛出。"""


class PlanValidationError(PlannerError):
    """模型多次生成后仍然存在确定性冲突时抛出。"""


class PlanItem(BaseModel):
    """每日计划中的一个日程项。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    start_time: str
    end_time: str
    priority: Literal["低", "中", "高", "low", "medium", "high"] = "中"
    category: str = Field(default="任务", min_length=1)
    notes: str = ""

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        try:
            return datetime.strptime(value, "%H:%M").strftime("%H:%M")
        except (TypeError, ValueError) as exc:
            raise ValueError("时间必须使用 24 小时制 HH:MM 格式") from exc

    @model_validator(mode="after")
    def validate_interval(self) -> PlanItem:
        start = datetime.strptime(self.start_time, "%H:%M")
        end = datetime.strptime(self.end_time, "%H:%M")
        if start >= end:
            raise ValueError("end_time 必须晚于 start_time")
        return self

    def to_validator_item(self) -> dict[str, str]:
        """转换为确定性日程冲突校验器使用的结构。"""

        return {
            "任务": self.title,
            "开始时间": self.start_time,
            "结束时间": self.end_time,
        }


class PlanDraft(BaseModel):
    """模型在附加工具元数据之前需要返回的精确 JSON 结构。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1)
    schedule: list[PlanItem] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_chronological_order(self) -> PlanDraft:
        starts = [item.start_time for item in self.schedule]
        if starts != sorted(starts):
            raise ValueError("schedule 必须按时间顺序排列")
        return self


class PlanValidation(BaseModel):
    is_valid: bool
    conflicts: list[str] = Field(default_factory=list)


class DailyPlan(BaseModel):
    """应用层最终拿到的结构化每日计划。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    date: date
    goal: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    schedule: list[PlanItem] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    calendar_events_considered: int = Field(ge=0)
    todo_items_considered: int = Field(default=0, ge=0)
    weather: dict[str, Any] | None = None
    validation: PlanValidation


PLANNER_PROMPT = """
你是一个每日生活规划 Agent。请根据用户目标和工具上下文，生成一个现实可执行的一日计划。

上下文规则：
- Calendar events 是固定日历事件，代表不可移动的占用时间，绝不能被挪动。
- Todo items 是用户今天想完成的待办事项，应在现实可行的情况下尽量安排进日程。
- User preferences 用来帮助个性化安排，例如作息、运动习惯、专注时间。
- Weather 只用于调整户外、旅行、通勤、运动等和天气有关的活动。

只返回一个 JSON object，结构必须严格如下：
{
  "summary": "简短说明今天计划的策略",
  "schedule": [
    {
      "title": "具体可执行的活动",
      "start_time": "HH:MM",
      "end_time": "HH:MM",
      "priority": "低|中|高",
      "category": "简短分类",
      "notes": "简短但有用的说明"
    }
  ],
  "assumptions": ["重要假设"]
}

要求：
- 时间必须使用 24 小时制 HH:MM。
- schedule 必须按时间顺序排列。
- 不要让新日程和固定日历事件重叠。
- 只安排新的用户活动，不要把固定日历事件重复写成新任务。
- 对 status 为“待处理”的 todo items，尽量安排进今天。
- 要考虑现实的过渡时间、吃饭、休息和恢复。
- 不要把一天排得过满。
- 不要编造工具没有提供的日历、天气、todo 或个人信息。
- 不要使用 Markdown 包裹 JSON。
- 不要在 JSON 外添加任何解释。
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
    """使用 OpenAI Agents SDK 工具调用和确定性校验生成每日计划。"""

    def __init__(
        self,
        text_agent: TextAgent | None = None,
        *,
        sdk_model: Any | None = None,
        calendar_getter: Callable[..., Any] = get_calendar_events,
        todo_getter: Callable[..., Any] = get_todos,
        weather_getter: Callable[..., Any] = get_weather,
        preference_getter: Callable[..., Any] = get_user_preferences,
        max_revision_attempts: int = 1,
    ) -> None:
        if max_revision_attempts < 0:
            raise ValueError("max_revision_attempts 不能小于 0")
        self.text_agent = text_agent
        self.sdk_model = sdk_model
        self.calendar_getter = calendar_getter
        self.todo_getter = todo_getter
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
        """理解用户目标，调用工具，并返回通过校验的每日计划。"""

        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must not be blank / goal 不能为空")

        plan_date = _normalise_date(target_date or date.today())
        clean_goal = goal.strip()
        clean_location = location.strip() if isinstance(location, str) else None
        if location is not None and not clean_location:
            raise ValueError("提供 location 时不能为空")

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
        """离线测试用路径：不调用真实 SDK，只使用文本 Agent 替身。"""

        calendar_events = await self._call_tool(
            "calendar",
            self.calendar_getter,
            plan_date,
        )
        todos = await self._call_tool("todo", self.todo_getter)
        preferences = await self._call_tool("preferences", self.preference_getter)

        tools_used = ["calendar", "todo", "preferences"]

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
                    "Weather was not checked because no location was provided / 因为没有提供 location，所以没有检查天气。"
                )

        initial_prompt = self._build_prompt(
            goal=clean_goal,
            target_date=plan_date,
            calendar_events=calendar_events,
            todos=todos,
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
                    todo_items_considered=len(todos),
                    weather=weather_context,
                    validation=validation,
                )
            conflicts = validation.conflicts

        conflict_text = "；".join(conflicts) or "未知冲突"
        raise PlanValidationError(
            f"Kimi could not produce a conflict-free plan / 无法生成无冲突计划：{conflict_text}"
        )

    async def _create_daily_plan_with_sdk(
        self,
        clean_goal: str,
        *,
        plan_date: str,
        location: str | None,
    ) -> DailyPlan:
        """通过 OpenAI Agents SDK 的 Agent、Runner 和工具调用生成计划。"""

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

            todos = tool_state["todos"]
            if todos is None:
                todos = await self._call_tool("todo", self.todo_getter)
                tool_state["todos"] = todos

            validation = self._validate_draft(draft, calendar_events)
            if validation.is_valid:
                tools_used = [
                    name
                    for name in ("calendar", "todo", "preferences", "weather")
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
                    todo_items_considered=len(todos),
                    weather=weather_context,
                    validation=validation,
                )

            conflicts = validation.conflicts

        conflict_text = "；".join(conflicts) or "未知冲突"
        raise PlanValidationError(
            f"Kimi could not produce a conflict-free plan / 无法生成无冲突计划：{conflict_text}"
        )

    async def _generate_draft(self, prompt: str) -> PlanDraft:
        """生成并解析 PlanDraft，保留离线 text_agent 兼容能力。"""

        structured_runner = getattr(self.text_agent, "run_structured", None)
        if callable(structured_runner):
            result = await structured_runner(prompt, PlanDraft)
            try:
                return PlanDraft.model_validate(result)
            except ValidationError as exc:
                raise PlannerOutputError(
                    f"Kimi 返回的每日计划结构无效：{exc}"
                ) from exc

        raw_output = await self.text_agent.run(prompt)
        return self._parse_draft(raw_output)

    async def _generate_draft_with_sdk(
        self,
        sdk_agent: Any,
        prompt: str,
    ) -> PlanDraft:
        """运行 OpenAI Agents SDK Agent，并归一化结构化输出。"""

        try:
            from agents import Runner
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI Agents SDK 未安装。请运行 "
                "`python -m pip install -r requirements.txt`。"
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
                f"Agents SDK 返回的每日计划结构无效：{exc}"
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
                "OpenAI Agents SDK 未安装。请运行 "
                "`python -m pip install -r requirements.txt`。"
            ) from exc

        set_tracing_disabled(disabled=True)
        tool_state: dict[str, Any] = {
            "calendar_events": None,
            "todos": None,
            "preferences": None,
            "weather": None,
            "calendar_used": False,
            "todo_used": False,
            "preferences_used": False,
            "weather_used": False,
        }

        @function_tool
        async def get_calendar_context() -> list[dict[str, Any]]:
            """返回目标日期的固定日历事件。"""

            tool_state["calendar_used"] = True
            events = await self._call_tool(
                "calendar",
                self.calendar_getter,
                plan_date,
            )
            tool_state["calendar_events"] = events
            return events

        @function_tool
        async def get_todo_context() -> list[dict[str, Any]]:
            """返回今天需要安排进日程的待办事项。"""

            tool_state["todo_used"] = True
            todos = await self._call_tool("todo", self.todo_getter)
            tool_state["todos"] = todos
            return todos

        @function_tool
        async def get_preferences_context() -> dict[str, Any]:
            """返回用户长期偏好，例如作息、运动习惯和专注时间。"""

            tool_state["preferences_used"] = True
            preferences = await self._call_tool(
                "preferences",
                self.preference_getter,
            )
            tool_state["preferences"] = preferences
            return preferences

        tools = [
            get_calendar_context,
            get_todo_context,
            get_preferences_context,
        ]

        routing_assumptions: list[str] = []
        if self._goal_needs_weather(goal):
            if location:

                @function_tool
                async def get_weather_context() -> dict[str, Any]:
                    """返回指定地点和日期的天气预报。"""

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
                    "Weather was not checked because no location was provided / 因为没有提供 location，所以没有检查天气。"
                )

        model = self.sdk_model
        if model is None:
            if not MOONSHOT_API_KEY:
                raise ValueError(
                    "缺少 MOONSHOT_API_KEY。请添加到 .env 或系统环境变量。"
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
            "在生成最终计划前，必须根据需要使用 calendar、todo 和 preference "
            "工具获取上下文。calendar events 是固定不可用时间。todo items 是用户"
            "今天希望完成的任务，应在现实可行时安排进计划。当天气工具可用，并且"
            "目标涉及户外、旅行、通勤或天气敏感活动时，使用 weather 工具。最终"
            "输出必须符合结构化输出 schema。"
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
            raise PlannerToolError(
                f"{name} tool failed / {name} 工具调用失败：{exc}"
            ) from exc

    @staticmethod
    def _build_prompt(
        *,
        goal: str,
        target_date: str,
        calendar_events: object,
        todos: object,
        preferences: object,
        weather: object,
    ) -> str:
        context = {
            "goal": goal,
            "date": target_date,
            "calendar_events": calendar_events,
            "todos": todos,
            "user_preferences": preferences,
            "weather": weather,
        }
        return (
            f"{PLANNER_PROMPT}\n\n计划上下文：\n"
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
            "因为这个目标和天气有关，请在生成计划前调用 weather 工具。"
            if needs_weather and location
            else "这个请求和天气有关，但没有提供 location，因此没有可用的 weather 工具。"
            if needs_weather
            else "这个请求不需要 weather 工具。"
        )
        return (
            f"请为 {target_date} 创建每日计划。\n"
            f"用户目标：{goal}\n"
            f"地点：{location or '未提供'}\n"
            "在生成计划前，请调用 calendar、todo 和 preference 工具。"
            f"{weather_instruction}"
        )

    @staticmethod
    def _build_revision_prompt(conflicts: list[str]) -> str:
        return (
            "请修改你上一次生成的 JSON 计划，移除以下确定性时间冲突 (conflicts)："
            f"{json.dumps(conflicts, ensure_ascii=False)}。"
            "只返回修正后的 JSON object，并使用完全相同的 schema。"
        )

    @staticmethod
    def _build_sdk_revision_prompt(
        *,
        goal: str,
        target_date: str,
        previous_draft: PlanDraft | None,
        conflicts: list[str],
    ) -> str:
        previous = previous_draft.model_dump(mode="json") if previous_draft else None
        return (
            f"请修正 {target_date} 的每日计划。\n"
            f"用户目标：{goal}\n"
            "上一版计划存在以下确定性时间冲突：\n"
            f"{json.dumps(conflicts, ensure_ascii=False)}\n"
            "上一版计划：\n"
            f"{json.dumps(previous, ensure_ascii=False, indent=2)}\n"
            "如有需要，请重新调用 calendar、todo 和 preference 工具，"
            "然后返回一个没有时间重叠的结构化计划。"
        )

    @staticmethod
    def _parse_draft(raw_output: str) -> PlanDraft:
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise PlannerOutputError("Kimi 返回了空的计划结果")

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
            raise PlannerOutputError("Kimi 没有返回有效的 JSON object")

        try:
            return PlanDraft.model_validate(parsed)
        except ValidationError as exc:
            raise PlannerOutputError(
                f"Kimi 返回的每日计划结构无效：{exc}"
            ) from exc

    @staticmethod
    def _validate_draft(
        draft: PlanDraft,
        calendar_events: object,
    ) -> PlanValidation:
        if not isinstance(calendar_events, list):
            raise PlannerToolError("calendar 工具必须返回 event list")

        schedule = [item.to_validator_item() for item in draft.schedule]
        for event in calendar_events:
            if not isinstance(event, dict):
                raise PlannerToolError("calendar event 必须是 dictionary")
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
                    f"calendar event 缺少必要字段：{exc.args[0]}"
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
        raise ValueError("target_date 必须是非空 ISO 日期")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("target_date 必须使用 YYYY-MM-DD 格式") from exc
