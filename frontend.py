"""Streamlit frontend for the Phase 1 Personal Planner MVP."""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import streamlit as st
from openai import APIConnectionError, AuthenticationError, RateLimitError

from memory.rag import (
    HashEmbeddingProvider,
    MemoryConsolidationError,
    PersonalRagStore,
)
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
from tools.edit_event import EditAction, EditEventStore, FeedbackScope
from tools.behavior_preference import MIN_SCHEDULING_CONFIDENCE
from tools.feedback_service import FeedbackService
from tools.memory_context import (
    DEFAULT_MEMORY_CONTROLS_PATH,
    LocalMemoryContextService,
    MemoryControlStore,
    RagMemoryContextService,
)
from tools.preference_aggregator import PreferenceAggregator
from tools.activity import activity_type as _activity_type
from planner_agents.validation import ensure_calendar_slot_available


BEHAVIOR_HISTORY_PATH = Path(__file__).resolve().parent / "data" / "behavior_history.jsonl"
FeedbackAction = Literal["accept", "move", "skip", "delete"]


def _planner_user() -> str:
    return str(st.session_state.get("planner_user_id", "default")).strip() or "default"


def _on_planner_user_change() -> None:
    """Cancel transient review/request state, never persisted user evidence."""
    st.session_state["planner_active_user_id"] = _planner_user()
    for key in list(st.session_state):
        if key in {
            "daily_plan", "planner_result", "planner_request", "feedback_message",
            "calendar_synced_plan_key", "planner_goal", "planner_date", "planner_location",
        } or key.startswith(("accept-", "skip-", "delete-", "start-", "end-", "move-")):
            st.session_state.pop(key, None)


def _complete_plan_request(request_id: str, user_id: str, plan: DailyPlan) -> bool:
    """Only the still-current request can publish a result to this session."""
    if (
        st.session_state.get("planner_request") != (request_id, user_id)
        or st.session_state.get("planner_active_user_id") != user_id
        or _planner_user() != user_id
    ):
        return False
    st.session_state["planner_result"] = {"user_id": user_id, "plan": plan}
    st.session_state.pop("planner_request", None)
    st.session_state.pop("calendar_synced_plan_key", None)
    return True


def _plan_is_current(plan: DailyPlan, user_id: str) -> bool:
    result = st.session_state.get("planner_result", {})
    return (
        result.get("plan") is plan
        and result.get("user_id") == user_id
        and st.session_state.get("planner_active_user_id") == user_id
        and _planner_user() == user_id
    )


def build_planner(
    demo_mode: bool,
    *,
    use_google_calendar: bool = False,
    use_todoist: bool = False,
    memory_service: LocalMemoryContextService | RagMemoryContextService | None = None,
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
    # Direct demo calls remain fully offline.  The Streamlit app supplies its
    # configured service explicitly, so normal app usage keeps OpenAI RAG.
    service = memory_service or (
        RagMemoryContextService(
            rag_store=PersonalRagStore(embedder=HashEmbeddingProvider())
        )
        if demo_mode
        else RagMemoryContextService()
    )
    if not demo_mode:
        return PlannerAgent(
            calendar_getter=calendar_getter,
            todo_getter=todo_getter,
            memory_getter=service.get_memory_context,
        )
    return PlannerAgent(
        DemoTextAgent(),
        calendar_getter=calendar_getter,
        todo_getter=todo_getter,
        memory_getter=service.get_memory_context,
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
            "Time": f"{item.start_time} - {item.end_time}",
            "Item": item.title,
            "Priority": item.priority,
            "Category": item.category,
            "Notes": item.notes,
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
    user_id: str = "default",
    scope: FeedbackScope = "unknown",
    reason: str | None = None,
) -> str:
    """Persist one feedback event and update the displayed plan in place."""

    if scope not in {"unknown", "temporary", "persistent"}:
        raise ValueError("Invalid feedback scope.")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("Feedback reason must be text.")
    try:
        item = plan.schedule[item_index]
    except IndexError as exc:
        raise ValueError("The selected schedule item does not exist.") from exc

    original_start = datetime.combine(
        plan.date, _parse_time(item.start_time)
    ).astimezone()
    original_end = datetime.combine(plan.date, _parse_time(item.end_time)).astimezone()
    fields = {
        "activity_type": _activity_type(item.title),
        "original_start": original_start,
        "original_end": original_end,
        "source_plan_id": f"daily-plan-{plan.date.isoformat()}",
        "user_id": user_id,
        "scope": scope,
        "reason": reason,
    }

    if action == "accept":
        feedback_service.accept(**fields)
        return f"Recorded ACCEPT for {item.title}."
    if action == "skip":
        feedback_service.skip(**fields)
        plan.schedule.pop(item_index)
        return f"Recorded SKIP for {item.title}."
    if action == "delete":
        if google_calendar is not None and google_event_id is not None:
            google_calendar.delete_event(google_event_id)
        feedback_service.delete(**fields)
        plan.schedule.pop(item_index)
        suffix = " and deleted it from Google Calendar." if google_event_id else "."
        return f"Recorded DELETE for {item.title}{suffix}"
    if new_start is None or new_end is None:
        raise ValueError("Select both a new start and end time.")

    move_start = datetime.combine(plan.date, new_start).astimezone()
    move_end = datetime.combine(plan.date, new_end).astimezone()
    if move_start >= move_end:
        raise ValueError("The end time must be later than the start time.")
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
    suffix = " and updated Google Calendar." if google_event_id else "."
    return f"Recorded MOVE for {item.title}{suffix}"


def _parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _ensure_no_schedule_conflict(
    plan: DailyPlan,
    selected_index: int,
    start: datetime,
    end: datetime,
) -> None:
    ensure_calendar_slot_available(plan, start.strftime("%H:%M"), end.strftime("%H:%M"))
    for index, item in enumerate(plan.schedule):
        if index == selected_index:
            continue
        item_start = datetime.combine(
            plan.date, _parse_time(item.start_time)
        ).astimezone()
        item_end = datetime.combine(plan.date, _parse_time(item.end_time)).astimezone()
        if start < item_end and item_start < end:
            raise ValueError(
                f'The new time conflicts with "{item.title}". Choose another time.'
            )


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
        if event.scope != "temporary":
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
    user_id: str = "default",
    memory_service: LocalMemoryContextService | RagMemoryContextService | None = None,
) -> DailyPlan:
    """Run the async planner from Streamlit's synchronous execution model."""

    service = memory_service or (
        RagMemoryContextService(
            rag_store=PersonalRagStore(embedder=HashEmbeddingProvider())
        )
        if demo_mode
        else RagMemoryContextService()
    )
    if isinstance(service, RagMemoryContextService):
        service.begin_session(
            user_id,
            f"daily-plan-{target_date.isoformat()}",
            goal,
        )

    planner = build_planner(
        demo_mode,
        use_google_calendar=use_google_calendar,
        use_todoist=use_todoist,
        memory_service=service,
    )
    return asyncio.run(
        planner.create_daily_plan(
            goal,
            target_date=target_date,
            location=location.strip() or None,
            user_id=user_id,
        )
    )


def render_plan(
    plan: DailyPlan,
    *,
    allow_calendar_sync: bool = False,
    user_id: str = "default",
) -> None:
    """Render a validated plan and its supporting context."""

    if not _plan_is_current(plan, user_id):
        return

    st.subheader(f"Your plan · {plan.date.isoformat()}")
    st.caption(f"Goal: {plan.goal}")
    st.write(plan.summary)
    status = "Conflict checks passed" if plan.validation.is_valid else "Conflicts found"
    if plan.validation.is_valid:
        st.success(status)
    else:
        st.error(status)
        for conflict in plan.validation.conflicts:
            st.write(f"• {conflict}")

    metric_columns = st.columns(4)
    metric_columns[0].metric("Plan items", len(plan.schedule))
    metric_columns[1].metric("Fixed events", plan.calendar_events_considered)
    metric_columns[2].metric("Todo items", plan.todo_items_considered)
    metric_columns[3].metric("Memory rules", plan.memory_preferences_considered)

    if plan.schedule:
        st.markdown("#### Schedule")
        st.dataframe(
            schedule_rows(plan),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Time": st.column_config.TextColumn(width="small"),
                "Item": st.column_config.TextColumn(width="medium"),
                "Notes": st.column_config.TextColumn(width="large"),
            },
        )
        render_feedback_controls(
            plan,
            sync_google_calendar=allow_calendar_sync,
            user_id=user_id,
        )
    else:
        st.info(
            "No activities remain in this plan. Update your goal above and "
            "choose Generate plan to start again. Your feedback has been saved."
        )
    render_learning_dashboard(user_id=user_id)
    if allow_calendar_sync and plan.schedule and plan.validation.is_valid:
        sync_key = calendar_sync_key(plan)
        sync_store = CalendarSyncStore()
        st.markdown("#### Sync to Google Calendar")
        st.caption("Events are created only after explicit confirmation.")
        try:
            already_synced = sync_store.is_synced(sync_key)
        except ValueError as exc:
            st.error(f"Unable to read the sync record: {exc}")
            already_synced = True
        if already_synced:
            st.success("This plan is already synced and will not be duplicated.")
        if st.button(
            "Synced" if already_synced else f"Confirm sync of {len(plan.schedule)} items",
            type="primary",
            disabled=already_synced,
        ):
            if not _plan_is_current(plan, user_id):
                return
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
                st.success(f"Created {len(created)} Google Calendar events.")
            except (CalendarConfigurationError, CalendarOperationError, ValueError) as exc:
                st.error(f"Google Calendar sync failed: {exc}")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Planning context")
        st.write(", ".join(plan.tools_used) or "None")
        with st.expander("How planning tools were used"):
            st.caption("This trace shows tool activity only; it does not expose private model reasoning.")
            if plan.tool_trace:
                st.dataframe(tool_trace_rows(plan), use_container_width=True, hide_index=True)
            else:
                st.caption("No tool execution trace is available for this plan.")
        if plan.assumptions:
            st.markdown("#### Assumptions")
            for assumption in plan.assumptions:
                st.write(f"• {assumption}")
    with right:
        st.markdown("#### Weather")
        if plan.weather and plan.weather.get("available", True):
            weather: dict[str, Any] = plan.weather
            st.write(f"**{weather.get('location', '')}** · {weather.get('condition', '')}")
            low = weather.get("temperature_low_c")
            high = weather.get("temperature_high_c")
            if low is not None and high is not None:
                st.write(f"{low}°C – {high}°C")
            st.caption(str(weather.get("planning_advice", "")))
        elif plan.weather:
            st.warning(str(plan.weather.get("message", "Weather data is unavailable.")))
        else:
            st.caption("This goal does not require weather context.")

    with st.expander("View complete JSON"):
        st.json(plan.model_dump(mode="json"))


def tool_trace_rows(plan: DailyPlan) -> list[dict[str, str]]:
    """Build a human-readable, payload-free tool audit table."""

    return [
        {
            "Tool": item.context_label.title(),
            "Purpose": item.description,
            "Permission": item.permission.value,
            "Status": item.status,
            "Cache": "reused" if item.cache_used else "fresh",
            "Fallback": "yes" if item.fallback_applied else "no",
            "Result": item.message,
        }
        for item in plan.tool_trace
    ]


def render_feedback_controls(
    plan: DailyPlan,
    *,
    sync_google_calendar: bool = False,
    user_id: str = "default",
) -> None:
    """Render interactive schedule feedback controls below a generated plan."""

    st.markdown("#### Adjust the plan and teach memory")
    st.caption(
        "Choose whether feedback is a one-off exception, an ongoing change, "
        "or unspecified. One-off exceptions do not train long-term preferences."
    )
    feedback_service = FeedbackService(EditEventStore(BEHAVIOR_HISTORY_PATH))

    for index, item in enumerate(plan.schedule):
        with st.expander(f"{item.start_time}–{item.end_time} · {item.title}"):
            feedback_key = f"{user_id}-{plan.date}-{item.title}-{item.start_time}-{item.end_time}"
            scope = st.selectbox(
                "How should this feedback affect future plans?",
                options=["unknown", "temporary", "persistent"],
                format_func=lambda value: {
                    "unknown": "Unspecified — learn from repeated feedback",
                    "temporary": "This time only — do not learn a habit",
                    "persistent": "Ongoing change — update my preference",
                }[value],
                key=f"feedback-scope-{feedback_key}",
            )
            reason = st.text_input("Reason (optional)", key=f"feedback-reason-{feedback_key}")
            accept_column, skip_column, delete_column = st.columns(3)
            if accept_column.button("Accept", key=f"accept-{index}"):
                _handle_feedback_action(
                    plan, index, "accept", feedback_service,
                    scope=scope, reason=reason,
                    sync_google_calendar=sync_google_calendar,
                    user_id=user_id,
                )
            if skip_column.button("Skip", key=f"skip-{index}"):
                _handle_feedback_action(
                    plan, index, "skip", feedback_service,
                    scope=scope, reason=reason,
                    sync_google_calendar=sync_google_calendar,
                    user_id=user_id,
                )
            if delete_column.button("Delete", key=f"delete-{index}"):
                _handle_feedback_action(
                    plan, index, "delete", feedback_service,
                    scope=scope, reason=reason,
                    sync_google_calendar=sync_google_calendar,
                    user_id=user_id,
                )

            start_column, end_column, move_column = st.columns(3)
            new_start = start_column.time_input(
                "New start", value=_parse_time(item.start_time), key=f"start-{index}"
            )
            new_end = end_column.time_input(
                "New end", value=_parse_time(item.end_time), key=f"end-{index}"
            )
            if move_column.button("Move to this time", key=f"move-{index}"):
                _handle_feedback_action(
                    plan,
                    index,
                    "move",
                    feedback_service,
                    scope=scope, reason=reason,
                    new_start=new_start,
                    new_end=new_end,
                    sync_google_calendar=sync_google_calendar,
                    user_id=user_id,
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
    user_id: str = "default",
    scope: FeedbackScope = "unknown",
    reason: str | None = None,
) -> None:
    if not _plan_is_current(plan, user_id):
        return
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
            user_id=user_id,
            scope=scope,
            reason=reason,
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
    if not _plan_is_current(plan, user_id):
        return
    st.session_state["feedback_message"] = message
    st.rerun()


def render_learning_dashboard(*, user_id: str = "default") -> None:
    """Show the local evidence and preferences produced by feedback actions."""

    summary = get_learning_summary(
        EditEventStore(BEHAVIOR_HISTORY_PATH),
        user_id=user_id,
    )
    required = summary["minimum_evidence"]
    action_counts: Counter[str] = summary["action_counts"]

    with st.expander("What memory has learned", expanded=False):
        st.caption(
            f"Unspecified feedback needs {required} compatible observations. "
            "An ongoing change updates a preference directly; one-off edits are excluded. "
            "Scheduling requires confidence of at least 70%."
        )
        metrics = st.columns(5)
        metrics[0].metric("Total feedback", summary["total_events"])
        for column, action in zip(metrics[1:], EditAction, strict=True):
            column.metric(action.value, action_counts[action.value])

        evidence_rows = [
            {
                "Activity": activity_type.replace("_", " "),
                "Feedback": action,
                "Non-temporary observations": count,
            }
            for (activity_type, action), count in sorted(summary["evidence"].items())
        ]
        if evidence_rows:
            st.markdown("##### Evidence progress")
            st.dataframe(evidence_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No feedback yet. Accept, skip, delete, or move a plan item.")

        preferences: list[dict[str, Any]] = summary["preferences"]
        if preferences:
            st.markdown("##### Learned preferences")
            for preference in preferences:
                activity = str(preference["activity_type"]).replace("_", " ")
                attribute = preference["attribute"]
                value = preference["value"]
                confidence = float(preference["confidence"])
                show = st.success if confidence >= MIN_SCHEDULING_CONFIDENCE else st.warning
                show(
                    f"{activity} · {attribute} · {value} "
                    f"(confidence {confidence:.0%}, "
                    f"{preference['evidence_count']} evidence events)"
                    + (" — below scheduling threshold" if confidence < MIN_SCHEDULING_CONFIDENCE else "")
                )
        else:
            st.caption("Preferences appear here after reaching the evidence threshold.")


def parse_preference_value(raw_value: str) -> dict[str, str]:
    """Parse and validate a learned-preference value edited in the UI."""

    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError("Preference value must be valid JSON") from exc
    if not isinstance(value, dict) or not value:
        raise ValueError("Preference value must be a non-empty JSON object")
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise ValueError("Preference value keys and values must be strings")
    return value


def render_memory_page(
    user_id: str,
    *,
    memory_service: LocalMemoryContextService | RagMemoryContextService | None = None,
) -> None:
    """Render inspect, edit, pause, resume, and forget controls for memory."""

    service = memory_service or LocalMemoryContextService(
        event_store=EditEventStore(BEHAVIOR_HISTORY_PATH),
        control_store=MemoryControlStore(DEFAULT_MEMORY_CONTROLS_PATH),
    )
    context = service.get_memory_context(user_id, "")
    st.header("Memory")
    st.caption(
        "Review explicit profile settings, learned preferences, and their "
        "supporting evidence. Paused memories remain visible but are ignored "
        "by the planner."
    )

    if isinstance(service, RagMemoryContextService):
        st.subheader("Preference history")
        versions = service.get_preference_history(user_id)
        if versions:
            st.dataframe([
                {
                    "Activity": version["snapshot"]["activity_type"],
                    "Attribute": version["snapshot"]["attribute"],
                    "Version": version["version"],
                    "Value": json.dumps(version["snapshot"]["value"], ensure_ascii=False),
                    "Status": version["status"],
                    "Confidence": version["snapshot"]["confidence"],
                    "Evidence": ", ".join(version["snapshot"]["evidence_event_ids"]),
                    "Observed from": version["valid_from"],
                    "Observed until": version["valid_to"],
                    "Replaces": version["replaces"],
                } for version in versions
            ], use_container_width=True, hide_index=True)
        else:
            st.caption("Preference changes will appear here as they are observed.")

        st.subheader("Long-term summary review")
        summaries = service.list_long_term_summaries(user_id)
        st.caption(
            "Summaries linked to changed preferences leave retrieval until reviewed. "
            "Keep historical context or archive it; archived and expired records remain inspectable."
        )
        for summary in summaries:
            with st.expander(f"{summary['title']} ({summary['status']})"):
                st.write(summary["content"])
                st.caption("Sources: " + ", ".join(summary["source_ids"]))
                if summary["status"] == "review_required":
                    st.warning(summary["review_reason"])
                    with st.form(f"review-{user_id}-{summary['memory_id']}"):
                        keep = st.form_submit_button("Keep as historical context")
                        archive = st.form_submit_button("Archive summary")
                    token_key = f"review-token-{user_id}-{summary['memory_id']}"
                    displayed_token = st.session_state.get(token_key, summary["review_token"])
                    if keep or archive:
                        try:
                            service.review_long_term_summary(
                                user_id, summary["memory_id"], "keep" if keep else "archive",
                                expected_review_token=displayed_token,
                            )
                        except (KeyError, ValueError) as exc:
                            st.error(str(exc))
                            st.session_state[token_key] = summary["review_token"]
                        else:
                            st.rerun()
                    else:
                        st.session_state[token_key] = summary["review_token"]
        if not summaries:
            st.caption("No reviewed long-term summaries yet.")

        st.subheader("LLM memory summaries")
        st.caption(
            "Summarize two or more unprocessed feedback events with Kimi. "
            "The result remains an explainable episodic memory, not a hard rule."
        )
        preview_key = f"memory-consolidation-preview:{user_id}"
        if st.button("Generate Kimi summary preview"):
            try:
                preview = service.preview_feedback_consolidation(user_id)
            except (MemoryConsolidationError, ValueError) as exc:
                st.error(str(exc))
            else:
                if preview is None:
                    st.info("At least two new feedback events are needed first.")
                else:
                    st.session_state[preview_key] = preview

        preview = st.session_state.get(preview_key)
        if preview is not None:
            st.info(
                f"Preview only — based on {len(preview.source_ids)} feedback events. "
                "Edit it if needed, then approve before it enters long-term memory."
            )
            with st.form(f"{preview_key}-form"):
                title = st.text_input("Summary title", value=preview.summary.title)
                content = st.text_area("Summary", value=preview.summary.summary)
                importance = st.slider(
                    "Importance", 0.0, 1.0, float(preview.summary.importance), 0.05
                )
                approve, discard = st.columns(2)
                approved = approve.form_submit_button("Approve and save to long-term memory")
                discarded = discard.form_submit_button("Discard preview")
            if discarded:
                del st.session_state[preview_key]
                st.rerun()
            if approved:
                try:
                    summary = service.confirm_feedback_consolidation(
                        user_id,
                        preview,
                        title=title,
                        content=content,
                        importance=importance,
                    )
                except (MemoryConsolidationError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    del st.session_state[preview_key]
                    st.success(f"Saved long-term summary: {summary['title']}")
                    st.rerun()

    profile = context["profile"]
    st.subheader("Profile memory")
    with st.form("profile-memory-form"):
        profile_values = {
            "timezone": st.text_input("Timezone", value=str(profile.get("timezone", "local"))),
            "wake_time": st.text_input("Wake time", value=str(profile.get("wake_time", "08:00"))),
            "sleep_time": st.text_input("Sleep time", value=str(profile.get("sleep_time", "23:00"))),
            "exercise_habit": st.text_input(
                "Exercise habit", value=str(profile.get("exercise_habit", "evening"))
            ),
            "focus_period": st.text_input(
                "Focus period", value=str(profile.get("focus_period", "morning"))
            ),
        }
        if st.form_submit_button("Save profile"):
            for key, value in profile_values.items():
                service.update_profile_preference(user_id, key, value.strip())
            st.success("Profile memory saved.")
            st.rerun()

    st.subheader("Behavioral preferences")
    preferences = context["preferences"]
    if not preferences:
        st.info(
            "No learned preferences yet. Add three compatible unspecified "
            "observations, or explicitly mark an ongoing change."
        )
    for preference in preferences:
        memory_id = str(preference["memory_id"])
        activity = str(preference["activity_type"]).replace("_", " ")
        status = str(preference["status"])
        with st.expander(
            f"{activity} - {preference['attribute']} ({status})",
            expanded=True,
        ):
            st.caption(
                f"Confidence {float(preference['confidence']):.0%}; "
                f"evidence {preference['evidence_count']}; ID {memory_id}"
            )
            if float(preference["confidence"]) < MIN_SCHEDULING_CONFIDENCE:
                st.warning("Conflicting evidence: this preference will not automatically adjust your schedule.")
            with st.form(f"edit-memory-{memory_id}"):
                raw_value = st.text_area(
                    "Value (JSON)",
                    value=json.dumps(preference["value"], indent=2),
                )
                if st.form_submit_button("Save preference"):
                    try:
                        service.update_behavior_preference(
                            user_id,
                            memory_id,
                            parse_preference_value(raw_value),
                        )
                    except (KeyError, ValueError) as exc:
                        st.error(str(exc))
                    else:
                        st.success("Behavioral preference updated.")
                        st.rerun()

            status_column, forget_column = st.columns(2)
            target_status = "active" if status == "paused" else "paused"
            status_label = "Resume" if status == "paused" else "Pause"
            if status_column.button(status_label, key=f"status-{memory_id}"):
                service.set_behavior_preference_status(
                    user_id,
                    memory_id,
                    target_status,
                )
                st.rerun()
            confirm_forget = forget_column.checkbox(
                "Confirm forget",
                key=f"confirm-forget-{memory_id}",
            )
            if forget_column.button(
                "Forget",
                key=f"forget-{memory_id}",
                disabled=not confirm_forget,
            ):
                service.forget_memory(user_id, memory_id)
                st.rerun()

    st.subheader("Recent evidence")
    if context["relevant_events"]:
        st.dataframe(
            context["relevant_events"],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No feedback evidence has been recorded for this user.")


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
    st.caption(
        "Turn goals, todos, calendar events, memory, and weather into an "
        "executable daily plan."
    )

    with st.sidebar:
        page = st.radio("Page", ("Planner", "Memory"))
        user_id = st.text_input(
            "User ID", value="default", key="planner_user_id",
            on_change=_on_planner_user_change,
        ).strip() or "default"
        st.header("Runtime settings")
        demo_mode = st.toggle(
            "Offline demo mode",
            value=True,
            help="Uses deterministic model output and mock weather without an API key.",
        )
        use_google_calendar = st.toggle(
            "Read Google Calendar",
            value=False,
            help="The first use opens a browser for read-only OAuth consent.",
        )
        use_todoist = st.toggle(
            "Read Todoist tasks",
            value=False,
            help="Reads open tasks using TODOIST_API_TOKEN from the local .env file.",
        )
        st.info(
            "Offline demo mode works immediately. Live mode requires a valid "
            "MOONSHOT_API_KEY in .env."
        )

    if st.session_state.get("planner_active_user_id") != user_id:
        _on_planner_user_change()

    memory_service = RagMemoryContextService(
        base_service=LocalMemoryContextService(
            event_store=EditEventStore(BEHAVIOR_HISTORY_PATH),
            control_store=MemoryControlStore(DEFAULT_MEMORY_CONTROLS_PATH),
        )
    )
    if page == "Memory":
        render_memory_page(user_id, memory_service=memory_service)
        return

    with st.form("planner_form"):
        goal = st.text_area(
            "What do you want to accomplish?", value=DEMO_GOAL, height=130,
            key="planner_goal",
        )
        date_column, location_column = st.columns(2)
        target_date = date_column.date_input("Plan date", value=date.today(), key="planner_date")
        location = location_column.text_input(
            "Location (for weather-sensitive goals)",
            placeholder="Example: Madison, WI",
            key="planner_location",
        )
        submitted = st.form_submit_button(
            "Generate plan",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if not goal.strip():
            st.warning("Enter a goal before generating a plan.")
            return
        try:
            request_id = uuid4().hex
            st.session_state["planner_request"] = (request_id, user_id)
            with st.spinner("Collecting context and applying constraints..."):
                generated_plan = generate_plan(
                    goal=goal,
                    target_date=target_date,
                    location=location,
                    demo_mode=demo_mode,
                    use_google_calendar=use_google_calendar,
                    use_todoist=use_todoist,
                    user_id=user_id,
                    memory_service=memory_service,
                )
                if not _complete_plan_request(request_id, user_id, generated_plan):
                    return
        except (CalendarConfigurationError, TodoistConfigurationError) as exc:
            st.error(f"Tool configuration failed: {exc}")
        except PlannerConfigurationError as exc:
            st.error(str(exc))
            st.info("Enable Offline demo mode in the sidebar to continue without an API key.")
        except AuthenticationError:
            st.error("Moonshot authentication failed. Check the API key in .env.")
        except (APIConnectionError, RateLimitError) as exc:
            st.error(f"The model service is temporarily unavailable: {exc}")
        except PlannerError as exc:
            st.error(f"Plan generation failed: {exc}")

    feedback_message = st.session_state.pop("feedback_message", None)
    if isinstance(feedback_message, str):
        st.success(feedback_message)

    plan = st.session_state.get("planner_result", {}).get("plan")
    if isinstance(plan, DailyPlan) and _plan_is_current(plan, user_id):
        st.divider()
        render_plan(
            plan,
            allow_calendar_sync=use_google_calendar,
            user_id=user_id,
        )
    else:
        st.info(
            "Start with a goal and plan date above, then choose Generate plan. "
            "Review the schedule here before adjusting it or syncing to Google Calendar."
        )


if __name__ == "__main__":
    main()
