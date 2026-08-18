"""Streamlit frontend for the Phase 1 Personal Planner MVP."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import streamlit as st
from openai import APIConnectionError, AuthenticationError, RateLimitError

from main import DEMO_GOAL, DemoTextAgent
from planner_agents.planner_agent import (
    DailyPlan,
    PlannerAgent,
    PlannerConfigurationError,
    PlannerError,
)
from tools.weather_tool import MockWeatherProvider, get_weather


def build_planner(demo_mode: bool) -> PlannerAgent:
    """Create either the deterministic demo planner or the configured live one."""

    if not demo_mode:
        return PlannerAgent()
    return PlannerAgent(
        DemoTextAgent(),
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


def generate_plan(
    *,
    goal: str,
    target_date: date,
    location: str,
    demo_mode: bool,
) -> DailyPlan:
    """Run the async planner from Streamlit's synchronous execution model."""

    planner = build_planner(demo_mode)
    return asyncio.run(
        planner.create_daily_plan(
            goal,
            target_date=target_date,
            location=location.strip() or None,
        )
    )


def render_plan(plan: DailyPlan) -> None:
    """Render a validated plan and its supporting context."""

    status = "已通过冲突校验" if plan.validation.is_valid else "存在冲突"
    st.success(status) if plan.validation.is_valid else st.error(status)
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
                )
        except PlannerConfigurationError as exc:
            st.error(str(exc))
            st.info("可以先开启左侧的“离线 Demo 模式”。")
        except AuthenticationError:
            st.error("Moonshot 身份验证失败，请检查 .env 中的 API Key。")
        except (APIConnectionError, RateLimitError) as exc:
            st.error(f"模型服务暂时不可用：{exc}")
        except PlannerError as exc:
            st.error(f"计划生成失败：{exc}")

    plan = st.session_state.get("daily_plan")
    if isinstance(plan, DailyPlan):
        st.divider()
        render_plan(plan)


if __name__ == "__main__":
    main()
