"""Headless checks for the Planner's review and empty states."""

from datetime import date
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import frontend
from planner_agents.contracts import DailyPlan, PlanItem, PlanValidation


def _plan_view():
    import streamlit as st
    from frontend import render_plan

    plan = st.session_state["test_plan"]
    st.session_state["planner_user_id"] = "default"
    st.session_state["planner_active_user_id"] = "default"
    st.session_state["planner_result"] = {"user_id": "default", "plan": plan}
    render_plan(plan, allow_calendar_sync=True)


def _planner_page():
    from frontend import main

    main()


@pytest.fixture
def plan():
    return DailyPlan(
        date=date(2026, 10, 24),
        goal="Study for the exam",
        summary="A focused morning",
        schedule=[PlanItem(title="Study", start_time="09:00", end_time="10:00")],
        calendar_events_considered=0,
        validation=PlanValidation(is_valid=True),
    )


@pytest.fixture(autouse=True)
def isolated_ui(monkeypatch, tmp_path):
    monkeypatch.setattr(frontend, "BEHAVIOR_HISTORY_PATH", tmp_path / "feedback.jsonl")
    monkeypatch.setattr(frontend, "render_learning_dashboard", lambda **kwargs: None)
    monkeypatch.setattr(frontend, "RagMemoryContextService", lambda **kwargs: None)
    monkeypatch.setattr(frontend, "CalendarSyncStore", lambda: _UnsyncedStore())


class _UnsyncedStore:
    def is_synced(self, key):
        return False


def test_first_visit_explains_how_to_generate_a_plan():
    app = AppTest.from_function(_planner_page).run()

    assert not app.exception
    assert any("Start with a goal and plan date" in item.value for item in app.info)
    assert not app.dataframe


def test_review_shows_date_goal_and_schedule(plan):
    app = AppTest.from_function(_plan_view)
    app.session_state["test_plan"] = plan
    app.run()

    assert not app.exception
    assert app.subheader[0].value == "Your plan · 2026-10-24"
    assert app.caption[0].value == "Goal: Study for the exam"
    assert app.dataframe[0].value.iloc[0]["Item"] == "Study"
    assert any(button.label == "Confirm sync of 1 items" for button in app.button)


def test_removing_last_activity_shows_recovery_without_edit_or_sync_controls(plan):
    # Skip/delete mutate this list in place, including removal of the last item.
    plan.schedule.clear()
    app = AppTest.from_function(_plan_view)
    app.session_state["test_plan"] = plan
    app.run()

    assert not app.exception
    assert any("No activities remain" in item.value for item in app.info)
    assert not app.dataframe
    assert not app.button


def test_conflicts_are_visible_and_cannot_be_synced(plan):
    plan.validation = PlanValidation(is_valid=False, conflicts=["Study overlaps a meeting"])
    app = AppTest.from_function(_plan_view)
    app.session_state["test_plan"] = plan
    app.run()

    assert not app.exception
    assert app.error[0].value == "Conflicts found"
    assert any("Study overlaps a meeting" in item.value for item in app.markdown)
    assert not any("sync" in button.label.lower() for button in app.button)


def test_switching_user_clears_review_and_sync_controls_but_keeps_saved_data(plan, monkeypatch, tmp_path):
    saved = tmp_path / "feedback.jsonl"
    saved.write_text("existing user evidence\n", encoding="utf-8")
    monkeypatch.setattr(frontend, "generate_plan", lambda **kwargs: plan)
    app = AppTest.from_function(_planner_page).run()
    app.button[0].click().run()
    assert app.dataframe
    app.session_state["feedback_message"] = "Old user's feedback"
    app.session_state["calendar_synced_plan_key"] = "old-sync"
    app.sidebar.text_input[0].set_value("member-b").run()

    assert not app.exception
    assert not app.dataframe
    assert not any(button.label in {"Accept", "Skip", "Delete"} for button in app.button)
    assert "planner_result" not in app.session_state
    assert "feedback_message" not in app.session_state
    assert "calendar_synced_plan_key" not in app.session_state
    assert saved.read_text(encoding="utf-8") == "existing user evidence\n"
    app.sidebar.text_input[0].set_value("default").run()
    assert not app.dataframe


def test_generation_and_feedback_use_new_identity(plan, monkeypatch):
    requests = []

    def generate(**kwargs):
        requests.append(kwargs["user_id"])
        return plan

    monkeypatch.setattr(frontend, "generate_plan", generate)
    app = AppTest.from_function(_planner_page).run()
    app.sidebar.text_input[0].set_value("member-b").run()
    app.button[0].click().run()
    next(button for button in app.button if button.label == "Accept").click().run()

    assert not app.exception
    assert requests == ["member-b"]
    events = frontend.EditEventStore(frontend.BEHAVIOR_HISTORY_PATH).list_events()
    assert len(events) == 1
    assert events[0].user_id == "member-b"


@pytest.mark.parametrize("switch_back", [False, True])
def test_late_result_cannot_publish_after_identity_switch(plan, monkeypatch, switch_back):
    current_user = ["default"]
    monkeypatch.setattr(frontend, "_planner_user", lambda: current_user[0])

    def delayed_generation(**kwargs):
        # Inject the identity change at the completion boundary; actual widget
        # callbacks are covered by the user-switch test above.
        current_user[0] = "member-b"
        frontend._on_planner_user_change()
        if switch_back:
            current_user[0] = kwargs["user_id"]
            frontend._on_planner_user_change()
        return plan

    monkeypatch.setattr(frontend, "generate_plan", delayed_generation)
    app = AppTest.from_function(_planner_page).run()
    app.button[0].click().run()

    assert not app.exception
    assert "planner_result" not in app.session_state
    assert "planner_request" not in app.session_state
    assert not app.dataframe


def test_stale_feedback_cannot_write_for_new_identity(plan, monkeypatch):
    monkeypatch.setattr(frontend.st, "session_state", {
        "planner_user_id": "member-b",
        "planner_active_user_id": "member-b",
        "planner_result": {"user_id": "default", "plan": plan},
    })
    feedback = Mock()
    google = Mock()
    monkeypatch.setattr(frontend, "GoogleCalendarProvider", google)

    frontend._handle_feedback_action(
        plan, 0, "accept", feedback, user_id="member-b", sync_google_calendar=True,
    )

    assert not feedback.mock_calls
    google.assert_not_called()
