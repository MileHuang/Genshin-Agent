"""Streamlit frontend for the Phase 1 Personal Planner MVP."""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

import streamlit as st
from openai import APIConnectionError, AuthenticationError, RateLimitError

from main import DEMO_GOAL, DemoTextAgent
from planner_agents.planner_agent import (
    DailyPlan,
    PlanItem,
    PlannerAgent,
    PlannerConfigurationError,
    PlannerError,
)
from tools.weather_tool import MockWeatherProvider, get_weather
from tools.calendar_tool import (
    CalendarConfigurationError,
    CalendarOperationError,
    GoogleCalendarProvider,
    get_calendar_events,
)
from tools.calendar_sync_store import CalendarSyncStore, build_plan_sync_key
from tools.todo_tool import TodoistConfigurationError, TodoistProvider, get_todos
from tools.edit_event import EditAction, EditEventStore
from tools.feedback_service import FeedbackService
from tools.preference_aggregator import PreferenceAggregator


BEHAVIOR_HISTORY_PATH = Path(__file__).resolve().parent / "data" / "behavior_history.jsonl"
FeedbackAction = Literal["accept", "move", "skip", "delete"]


def build_planner(
    demo_mode: bool,
    *,
    use_google_calendar: bool = False,
    use_todoist: bool = False,
) -> PlannerAgent:
    """Create either the deterministic demo planner or the configured live one."""

    calendar_getter = (
        (lambda target_date: get_calendar_events(
            target_date, provider=GoogleCalendarProvider()
        ))
        if use_google_calendar
        else get_calendar_events
    )
    todo_getter = (
        (lambda: get_todos(provider=TodoistProvider()))
        if use_todoist
        else get_todos
    )
    if not demo_mode:
        return PlannerAgent(
            calendar_getter=calendar_getter,
            todo_getter=todo_getter,
        )
    return PlannerAgent(
        DemoTextAgent(),
        calendar_getter=calendar_getter,
        todo_getter=todo_getter,
        weather_getter=lambda location, target_date: get_weather(
            location,
            target_date,
            provider=MockWeatherProvider(),
        ),
    )


def schedule_rows(plan: DailyPlan) -> list[dict[str, str]]:
    """Convert a plan into display-friendly table rows."""

    return [
        {
            "时间": f"{item.start_time} – {item.end_time}",
            "事项": item.title,
            "优先级": item.priority,
            "分类": item.category,
            "备注": item.notes,
        }
        for item in plan.schedule
    ]


def calendar_sync_items(plan: DailyPlan) -> list[dict[str, str]]:
    """Return the schedule payload that will be sent to Google Calendar."""

    return [
        {
            "title": item.title,
            "start_time": item.start_time,
            "end_time": item.end_time,
            "notes": item.notes,
        }
        for item in plan.schedule
    ]


def calendar_sync_key(plan: DailyPlan) -> str:
    """Return a stable ID identifying the exact plan sent to Google Calendar."""

    return build_plan_sync_key(plan.date.isoformat(), calendar_sync_items(plan))


def apply_schedule_feedback(
    plan: DailyPlan,
    *,
    item_index: int,
    action: FeedbackAction,
    feedback_service: FeedbackService,
    new_start: time | None = None,
    new_end: time | None = None,
    google_calendar: GoogleCalendarProvider | None = None,
    google_event_id: str | None = None,
) -> str:
    """Persist one feedback event and update the displayed plan in place."""

    try:
        item = plan.schedule[item_index]
    except IndexError as exc:
        raise ValueError("找不到要调整的日程项。") from exc

    original_start = datetime.combine(
        plan.date, _parse_time(item.start_time)
    ).astimezone()
    original_end = datetime.combine(plan.date, _parse_time(item.end_time)).astimezone()
    fields = {
        "activity_type": _activity_type(item.title),
        "original_start": original_start,
        "original_end": original_end,
        "source_plan_id": f"daily-plan-{plan.date.isoformat()}",
    }

    if action == "accept":
        feedback_service.accept(**fields)
        return f"已记录 ACCEPT：{item.title}。"
    if action == "skip":
        feedback_service.skip(**fields)
        plan.schedule.pop(item_index)
        return f"已记录 SKIP：{item.title}。"
    if action == "delete":
        if google_calendar is not None and google_event_id is not None:
            google_calendar.delete_event(google_event_id)
        feedback_service.delete(**fields)
        plan.schedule.pop(item_index)
        suffix = "，并已从 Google Calendar 删除。" if google_event_id else "。"
        return f"已记录 DELETE：{item.title}{suffix}"
    if new_start is None or new_end is None:
        raise ValueError("请选择新的开始和结束时间。")

    move_start = datetime.combine(plan.date, new_start).astimezone()
    move_end = datetime.combine(plan.date, new_end).astimezone()
    if move_start >= move_end:
        raise ValueError("结束时间必须晚于开始时间。")
    _ensure_no_schedule_conflict(plan, item_index, move_start, move_end)
    if google_calendar is not None and google_event_id is not None:
        google_calendar.update_event(
            google_event_id,
            plan.date.isoformat(),
            {
                "title": item.title,
                "start_time": new_start.strftime("%H:%M"),
                "end_time": new_end.strftime("%H:%M"),
                "notes": item.notes,
            },
        )
    feedback_service.move(
        **fields,
        new_start=move_start,
        new_end=move_end,
    )
    item.start_time = new_start.strftime("%H:%M")
    item.end_time = new_end.strftime("%H:%M")
    plan.schedule.sort(key=lambda schedule_item: schedule_item.start_time)
    suffix = "，并已更新 Google Calendar。" if google_event_id else "。"
    return f"已记录 MOVE：{item.title}{suffix}"


def _parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _activity_type(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_")
    return normalized or "general"


def _ensure_no_schedule_conflict(
    plan: DailyPlan,
    selected_index: int,
    start: datetime,
    end: datetime,
) -> None:
    for index, item in enumerate(plan.schedule):
        if index == selected_index:
            continue
        item_start = datetime.combine(
            plan.date, _parse_time(item.start_time)
        ).astimezone()
        item_end = datetime.combine(plan.date, _parse_time(item.end_time)).astimezone()
        if start < item_end and item_start < end:
            raise ValueError(f"新时间与“{item.title}”冲突，请选择其他时间。")


def get_learning_summary(
    event_store: EditEventStore,
    *,
    user_id: str = "default",
    minimum_evidence: int = 3,
) -> dict[str, Any]:
    """Build display data for local feedback evidence and learned preferences."""

    events = event_store.list_events(user_id=user_id)
    action_counts = Counter(event.action.value for event in events)
    evidence: dict[tuple[str, str], int] = defaultdict(int)
    for event in events:
        evidence[(event.activity_type, event.action.value)] += 1
    preferences = PreferenceAggregator(minimum_evidence=minimum_evidence).aggregate(events)
    return {
        "total_events": len(events),
        "action_counts": action_counts,
        "evidence": evidence,
        "preferences": [preference.to_dict() for preference in preferences],
        "minimum_evidence": minimum_evidence,
    }


def generate_plan(
    *,
    goal: str,
    target_date: date,
    location: str,
    demo_mode: bool,
    use_google_calendar: bool = False,
    use_todoist: bool = False,
) -> DailyPlan:
    """Run the async planner from Streamlit's synchronous execution model."""

    planner = build_planner(
        demo_mode,
        use_google_calendar=use_google_calendar,
        use_todoist=use_todoist,
    )
    return asyncio.run(
        planner.create_daily_plan(
            goal,
            target_date=target_date,
            location=location.strip() or None,
        )
    )


def render_plan(plan: DailyPlan, *, allow_calendar_sync: bool = False) -> None:
    """Render a validated plan and its supporting context."""

    status = "已通过冲突校验" if plan.validation.is_valid else "存在冲突"
    if plan.validation.is_valid:
        st.success(status)
    else:
        st.error(status)
    st.subheader(plan.summary)

    metric_columns = st.columns(4)
    metric_columns[0].metric("日程项", len(plan.schedule))
    metric_columns[1].metric("固定事件", plan.calendar_events_considered)
    metric_columns[2].metric("待办事项", plan.todo_items_considered)
    metric_columns[3].metric("使用工具", len(plan.tools_used))

    st.dataframe(
        schedule_rows(plan),
        use_container_width=True,
        hide_index=True,
        column_config={
            "时间": st.column_config.TextColumn(width="small"),
            "事项": st.column_config.TextColumn(width="medium"),
            "备注": st.column_config.TextColumn(width="large"),
        },
    )

    render_feedback_controls(plan, sync_google_calendar=allow_calendar_sync)
    render_learning_dashboard()
    if allow_calendar_sync:
        sync_key = calendar_sync_key(plan)
        sync_store = CalendarSyncStore()
        st.markdown("#### 同步到 Google Calendar")
        st.caption("只会在你点击确认后创建当前计划中的日程。")
        try:
            already_synced = sync_store.is_synced(sync_key)
        except ValueError as exc:
            st.error(f"无法读取同步记录：{exc}")
            already_synced = True
        if already_synced:
            st.success("这份计划已同步到 Google Calendar，不会重复创建。")
        if st.button(
            "已同步" if already_synced else f"确认同步 {len(plan.schedule)} 项日程",
            type="primary",
            disabled=already_synced,
        ):
            try:
                items = calendar_sync_items(plan)
                created = GoogleCalendarProvider().create_events(
                    plan.date.isoformat(), items
                )
                sync_store.record(
                    sync_key,
                    target_date=plan.date.isoformat(),
                    event_ids=created,
                )
                st.session_state["calendar_synced_plan_key"] = sync_key
                st.success(f"已创建 {len(created)} 项 Google Calendar 日程。")
            except (CalendarConfigurationError, CalendarOperationError, ValueError) as exc:
                st.error(f"Google Calendar 同步失败：{exc}")

    left, right = st.columns(2)
    with left:
        st.markdown("#### 规划依据")
        st.write("、".join(plan.tools_used) or "无")
        if plan.assumptions:
            st.markdown("#### 重要假设")
            for assumption in plan.assumptions:
                st.write(f"• {assumption}")
    with right:
        st.markdown("#### 天气")
        if plan.weather:
            weather: dict[str, Any] = plan.weather
            st.write(f"**{weather.get('location', '')}** · {weather.get('condition', '')}")
            low = weather.get("temperature_low_c")
            high = weather.get("temperature_high_c")
            if low is not None and high is not None:
                st.write(f"{low}°C – {high}°C")
            st.caption(str(weather.get("planning_advice", "")))
        else:
            st.caption("本次目标不需要天气信息。")

    with st.expander("查看完整 JSON"):
        st.json(plan.model_dump(mode="json"))


def render_feedback_controls(
    plan: DailyPlan,
    *,
    sync_google_calendar: bool = False,
) -> None:
    """Render interactive schedule feedback controls below a generated plan."""

    st.markdown("#### 调整计划并帮助我学习")
    st.caption("接受、跳过、删除或移动日程；操作会保存为你的偏好学习证据。")
    feedback_service = FeedbackService(EditEventStore(BEHAVIOR_HISTORY_PATH))

    for index, item in enumerate(plan.schedule):
        with st.expander(f"{item.start_time}–{item.end_time} · {item.title}"):
            accept_column, skip_column, delete_column = st.columns(3)
            if accept_column.button("接受", key=f"accept-{index}"):
                _handle_feedback_action(
                    plan, index, "accept", feedback_service,
                    sync_google_calendar=sync_google_calendar,
                )
            if skip_column.button("跳过", key=f"skip-{index}"):
                _handle_feedback_action(
                    plan, index, "skip", feedback_service,
                    sync_google_calendar=sync_google_calendar,
                )
            if delete_column.button("删除", key=f"delete-{index}"):
                _handle_feedback_action(
                    plan, index, "delete", feedback_service,
                    sync_google_calendar=sync_google_calendar,
                )

            start_column, end_column, move_column = st.columns(3)
            new_start = start_column.time_input(
                "新开始时间", value=_parse_time(item.start_time), key=f"start-{index}"
            )
            new_end = end_column.time_input(
                "新结束时间", value=_parse_time(item.end_time), key=f"end-{index}"
            )
            if move_column.button("移动到此时间", key=f"move-{index}"):
                _handle_feedback_action(
                    plan,
                    index,
                    "move",
                    feedback_service,
                    new_start=new_start,
                    new_end=new_end,
                    sync_google_calendar=sync_google_calendar,
                )


def _handle_feedback_action(
    plan: DailyPlan,
    item_index: int,
    action: FeedbackAction,
    feedback_service: FeedbackService,
    *,
    new_start: time | None = None,
    new_end: time | None = None,
    sync_google_calendar: bool = False,
) -> None:
    try:
        original_sync_key = calendar_sync_key(plan)
        sync_store = CalendarSyncStore()
        sync_record = sync_store.get(original_sync_key) if sync_google_calendar else None
        event_ids = sync_record.get("event_ids", []) if sync_record else []
        event_id = event_ids[item_index] if item_index < len(event_ids) else None
        item_event_ids = {
            id(item): event_ids[index]
            for index, item in enumerate(plan.schedule)
            if index < len(event_ids)
        }
        message = apply_schedule_feedback(
            plan,
            item_index=item_index,
            action=action,
            feedback_service=feedback_service,
            new_start=new_start,
            new_end=new_end,
            google_calendar=GoogleCalendarProvider() if event_id else None,
            google_event_id=event_id,
        )
        if sync_record and action != "accept":
            sync_store.replace(
                original_sync_key,
                calendar_sync_key(plan),
                target_date=plan.date.isoformat(),
                event_ids=[item_event_ids[id(item)] for item in plan.schedule if id(item) in item_event_ids],
            )
    except (CalendarConfigurationError, CalendarOperationError, ValueError) as exc:
        st.error(str(exc))
        return
    st.session_state["feedback_message"] = message
    st.rerun()


def render_learning_dashboard() -> None:
    """Show the local evidence and preferences produced by feedback actions."""

    summary = get_learning_summary(EditEventStore(BEHAVIOR_HISTORY_PATH))
    required = summary["minimum_evidence"]
    action_counts: Counter[str] = summary["action_counts"]

    with st.expander("我学到了什么", expanded=False):
        st.caption(f"同一活动的同类反馈达到 {required} 次后，才会形成长期偏好。")
        metrics = st.columns(5)
        metrics[0].metric("总反馈", summary["total_events"])
        for column, action in zip(metrics[1:], EditAction, strict=True):
            column.metric(action.value, action_counts[action.value])

        evidence_rows = [
            {
                "活动": activity_type.replace("_", " "),
                "反馈": action,
                "证据次数": count,
                "距离学习": max(0, required - count),
            }
            for (activity_type, action), count in sorted(summary["evidence"].items())
        ]
        if evidence_rows:
            st.markdown("##### 证据进度")
            st.dataframe(evidence_rows, use_container_width=True, hide_index=True)
        else:
            st.info("还没有反馈记录。先对一项日程点击接受、跳过、删除或移动。")

        preferences: list[dict[str, Any]] = summary["preferences"]
        if preferences:
            st.markdown("##### 已形成的偏好")
            for preference in preferences:
                activity = str(preference["activity_type"]).replace("_", " ")
                attribute = preference["attribute"]
                value = preference["value"]
                confidence = float(preference["confidence"])
                st.success(
                    f"{activity} · {attribute} · {value} "
                    f"（置信度 {confidence:.0%}，证据 {preference['evidence_count']} 次）"
                )
        else:
            st.caption("达到证据阈值后，已形成的偏好会显示在这里。")


def main() -> None:
    st.set_page_config(
        page_title="Personal Planner",
        page_icon="🗓️",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        .block-container {max-width: 1100px; padding-top: 2rem;}
        [data-testid="stMetric"] {background: #f6f8fb; border: 1px solid #e5e9f0;
            padding: 0.8rem 1rem; border-radius: 0.8rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("🗓️ Personal Planner")
    st.caption("把目标、待办、日历、偏好和天气整理成一份可执行的每日计划。")

    with st.sidebar:
        st.header("运行设置")
        demo_mode = st.toggle(
            "离线 Demo 模式",
            value=True,
            help="无需 API Key，使用固定模型输出和模拟天气。",
        )
        use_google_calendar = st.toggle(
            "读取 Google Calendar",
            value=False,
            help="首次使用会打开浏览器，请使用自己的 Google 账号授权只读日历权限。",
        )
        use_todoist = st.toggle(
            "读取 Todoist 待办",
            value=False,
            help="从本地 .env 的 TODOIST_API_TOKEN 读取未完成待办。",
        )
        st.info(
            "离线 Demo 可立即体验界面。在线模式需要在 .env 中配置有效的 "
            "MOONSHOT_API_KEY。"
        )

    with st.form("planner_form"):
        goal = st.text_area("今天想完成什么？", value=DEMO_GOAL, height=130)
        date_column, location_column = st.columns(2)
        target_date = date_column.date_input("计划日期", value=date.today())
        location = location_column.text_input(
            "地点（天气相关目标时填写）",
            placeholder="例如：Madison, WI",
        )
        submitted = st.form_submit_button(
            "生成计划",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if not goal.strip():
            st.warning("请先输入今天的目标。")
            return
        try:
            with st.spinner("正在整理日程与约束…"):
                st.session_state["daily_plan"] = generate_plan(
                    goal=goal,
                    target_date=target_date,
                    location=location,
                    demo_mode=demo_mode,
                    use_google_calendar=use_google_calendar,
                    use_todoist=use_todoist,
                )
                # A newly generated plan is eligible for one explicit sync.
                st.session_state.pop("calendar_synced_plan_key", None)
        except (CalendarConfigurationError, TodoistConfigurationError) as exc:
            st.error(f"工具设置失败：{exc}")
        except PlannerConfigurationError as exc:
            st.error(str(exc))
            st.info("可以先开启左侧的“离线 Demo 模式”。")
        except AuthenticationError:
            st.error("Moonshot 身份验证失败，请检查 .env 中的 API Key。")
        except (APIConnectionError, RateLimitError) as exc:
            st.error(f"模型服务暂时不可用：{exc}")
        except PlannerError as exc:
            st.error(f"计划生成失败：{exc}")

    feedback_message = st.session_state.pop("feedback_message", None)
    if isinstance(feedback_message, str):
        st.success(feedback_message)

    plan = st.session_state.get("daily_plan")
    if isinstance(plan, DailyPlan):
        st.divider()
        render_plan(plan, allow_calendar_sync=use_google_calendar)


if __name__ == "__main__":
    main()
