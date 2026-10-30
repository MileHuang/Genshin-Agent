"""Regression checks for durable versions, stale summaries, and index boundaries."""

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from memory.rag import (
    ConsolidatedMemory, HashEmbeddingProvider, MemoryConsolidationError, MemoryDocument, PersonalRagStore,
)
from memory.session_memory import SessionMemoryStore
from tools.edit_event import EditEvent, EditEventStore
from tools.memory_context import LocalMemoryContextService, MemoryControlStore, RagMemoryContextService


NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


class CountingEmbedder(HashEmbeddingProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        return super().embed(text)


class Summarizer:
    def summarize(self, user_id, events):
        return ConsolidatedMemory(title="Gym feedback", summary="Gym was moved to evening.", importance=0.7)


def _service(tmp_path, *, clock=None, embedder=None):
    return RagMemoryContextService(
        base_service=LocalMemoryContextService(
            event_store=EditEventStore(tmp_path / "events.jsonl"),
            control_store=MemoryControlStore(tmp_path / "controls.json"),
            clock=clock or (lambda: NOW),
        ),
        rag_store=PersonalRagStore(tmp_path / "memory.db", embedder=embedder),
        session_store=SessionMemoryStore(tmp_path / "sessions"), summarizer=Summarizer(),
    )


def _event(service, age, hour=20, *, scope="unknown", activity="gym", user="alice"):
    timestamp = NOW - timedelta(days=age)
    event = EditEvent(
        action="MOVE", activity_type=activity,
        original_start=timestamp.replace(hour=12), original_end=timestamp.replace(hour=13),
        new_start=timestamp.replace(hour=hour), new_end=timestamp.replace(hour=hour + 1),
        source_plan_id=f"plan-{age}", user_id=user, scope=scope, timestamp=timestamp,
    )
    service.base_service.event_store.append(event)
    return event


def _old_habit(service):
    for age in (6, 5, 4):
        _event(service, age)
    return service.get_memory_context("alice", "gym")["preferences"][0]


def _summary(service):
    preview = service.preview_feedback_consolidation("alice")
    return service.confirm_feedback_consolidation("alice", preview)


def _chunks(store):
    with sqlite3.connect(store.path) as connection:
        return connection.execute("SELECT count(*) FROM memory_chunks").fetchone()[0]


def test_candidates_are_never_embedded_even_on_rebuild_or_provider_migration(tmp_path):
    embedder = CountingEmbedder()
    store = PersonalRagStore(tmp_path / "memory.db", embedder=embedder)
    doc = MemoryDocument(user_id="alice", memory_id="candidate", title="Raw", content="Private raw feedback", status="candidate")
    store.upsert(doc)
    assert store.rebuild_index() == 0
    assert store.retrieve("alice", "raw feedback") == []
    assert embedder.calls == []
    # Simulate a legacy index containing a vector for a candidate.
    with sqlite3.connect(store.path) as connection:
        connection.execute("INSERT INTO memory_chunks VALUES ('legacy', 'candidate', 0, 'raw', '[1]')")
        connection.execute("UPDATE rag_metadata SET value = 'hash:256'")
    reopened = PersonalRagStore(store.path, embedder=embedder)
    assert _chunks(reopened) == 0
    assert embedder.calls == []


def test_promotion_pause_and_resume_manage_vectors_and_failed_promotion_rolls_back(tmp_path):
    embedder = CountingEmbedder()
    store = PersonalRagStore(tmp_path / "memory.db", embedder=embedder)
    store.upsert(MemoryDocument(user_id="alice", memory_id="test", title="Test", content="Gym", status="candidate"))
    store.set_status("alice", "test", "active")
    assert len(embedder.calls) == 1 and _chunks(store) == 1
    store.set_status("alice", "test", "paused")
    assert _chunks(store) == 0
    store.set_status("alice", "test", "active")
    assert _chunks(store) == 1
    store.set_status("alice", "test", "candidate")

    def fail(_):
        raise RuntimeError("embedding unavailable")

    embedder.embed = fail
    with pytest.raises(RuntimeError, match="unavailable"):
        store.set_status("alice", "test", "active")
    assert store.list_documents("alice")[0]["status"] == "candidate"
    assert _chunks(store) == 0


def test_preference_versions_are_idempotent_durable_and_user_scoped(tmp_path):
    service = _service(tmp_path)
    original = _old_habit(service)
    first = service.get_preference_history("alice")
    service.get_memory_context("alice", "gym")
    assert service.get_preference_history("alice") == first
    _event(service, 1, 8, scope="persistent")
    service.get_memory_context("alice", "gym")
    history = service.get_preference_history("alice")
    assert [row["version"] for row in history] == [2, 1]
    assert history[0]["replaces"] == history[1]["version_id"]
    assert history[1]["status"] == "superseded" and history[1]["valid_to"] is not None
    assert history[0]["snapshot"]["value"]["start"] == "08:00"
    assert history[1]["snapshot"]["value"] == original["value"]
    assert _service(tmp_path).get_preference_history("alice") == history
    assert service.get_preference_history("bob") == []


@pytest.mark.parametrize("decision, status", [("keep", "active"), ("archive", "superseded")])
def test_changed_preference_quarantines_summary_until_explicit_review(tmp_path, decision, status):
    service = _service(tmp_path)
    _old_habit(service)
    saved = _summary(service)
    assert service.get_memory_context("alice", "gym")["relevant_memories"]
    _event(service, 1, 8, scope="persistent")
    context = service.get_memory_context("alice", "gym")
    assert context["preferences"][0]["value"]["start"] == "08:00"
    assert context["relevant_memories"] == []
    queued = service.list_long_term_summaries("alice")[0]
    assert queued["status"] == "review_required"
    assert queued["review_reason"] and _chunks(service.rag_store) == 0
    service.review_long_term_summary("alice", saved["memory_id"], decision,
                                     expected_review_token=queued["review_token"])
    assert service.list_long_term_summaries("alice")[0]["status"] == status
    assert bool(service.get_memory_context("alice", "gym")["relevant_memories"]) == (decision == "keep")
    with sqlite3.connect(service.rag_store.path) as connection:
        assert connection.execute("SELECT decision FROM summary_reviews").fetchone()[0] == decision


def test_new_change_invalidates_open_review_even_with_identical_clock_timestamp(tmp_path):
    service = _service(tmp_path)
    _old_habit(service)
    _summary(service)
    _event(service, 2, 8, scope="persistent")
    stale = service.list_long_term_summaries("alice")[0]
    _event(service, 1, 7, scope="persistent")
    with pytest.raises(ValueError, match="changed"):
        service.review_long_term_summary("alice", stale["memory_id"], "keep",
                                         expected_review_token=stale["review_token"])
    current = service.list_long_term_summaries("alice")[0]
    assert current["status"] == "review_required" and current["review_token"] != stale["review_token"]
    with pytest.raises(KeyError):
        service.rag_store.review_summary("bob", current["memory_id"], "keep",
                                         expected_review_token=current["review_token"])


def test_temporary_exception_does_not_version_or_quarantine_existing_habit(tmp_path):
    service = _service(tmp_path)
    _old_habit(service)
    _summary(service)
    before = service.get_preference_history("alice")
    _event(service, 1, 8, scope="temporary")
    assert service.get_preference_history("alice") == before
    assert service.list_long_term_summaries("alice")[0]["status"] == "active"


def test_forgetting_suppresses_sources_from_all_preference_versions(tmp_path):
    service = _service(tmp_path)
    original = _old_habit(service)
    _summary(service)
    _event(service, 1, 8, scope="persistent")
    service.get_memory_context("alice", "gym")
    service.forget_memory("alice", original["memory_id"])
    assert service.list_long_term_summaries("alice") == []
    context = service.get_memory_context("alice", "gym")
    assert context["preferences"] == [] and context["relevant_memories"] == []
    history = service.get_preference_history("alice")
    assert history[0]["status"] == "forgotten"
    assert history[1]["status"] == "superseded"


def test_old_candidates_are_expired_and_not_in_planner_or_preview_but_audit_remains(tmp_path):
    embedder = CountingEmbedder()
    service = _service(tmp_path, embedder=embedder)
    old = _event(service, 31)
    _event(service, 30)
    recent = _event(service, 1, 8)
    context = service.get_memory_context("alice", "gym")
    assert [event["event_id"] for event in context["relevant_events"]] == [recent.event_id]
    assert service.preview_feedback_consolidation("alice") is None
    docs = {item["memory_id"]: item for item in service.rag_store.list_documents("alice")}
    assert docs[f"edit-event:{old.event_id}"]["status"] == "expired"
    assert len(service.base_service.event_store.list_events()) == 3
    assert embedder.calls == []


def test_expired_active_memory_loses_vectors_and_is_still_inspectable(tmp_path):
    service = _service(tmp_path)
    service.rag_store.upsert(MemoryDocument(
        user_id="alice", title="Expired context", content="gym context", memory_id="old",
        occurred_at=NOW - timedelta(days=3), expires_at=NOW - timedelta(days=1),
    ))
    assert _chunks(service.rag_store) == 1
    assert service.get_memory_context("alice", "gym")["relevant_memories"] == []
    assert service.rag_store.list_documents("alice")[0]["status"] == "expired"
    assert _chunks(service.rag_store) == 0


def test_preview_cannot_promote_feedback_that_expired_while_reviewing(tmp_path):
    clock = [NOW]
    service = _service(tmp_path, clock=lambda: clock[0])
    _event(service, 2)
    _event(service, 1)
    preview = service.preview_feedback_consolidation("alice")
    clock[0] += timedelta(days=31)
    with pytest.raises(MemoryConsolidationError, match="changed"):
        service.confirm_feedback_consolidation("alice", preview)
    assert service.list_long_term_summaries("alice") == []


def test_manual_edit_pause_and_resume_are_versioned(tmp_path):
    service = _service(tmp_path)
    preference = _old_habit(service)
    service.update_behavior_preference("alice", preference["memory_id"], {"start": "08:00", "end": "09:00"})
    service.set_behavior_preference_status("alice", preference["memory_id"], "paused")
    service.set_behavior_preference_status("alice", preference["memory_id"], "active")
    history = service.get_preference_history("alice")
    assert [version["snapshot"]["status"] for version in history] == ["active", "paused", "active", "active"]
    assert len(history) == 4


def test_initial_snapshot_flags_legacy_summary_with_disjoint_older_sources(tmp_path):
    service = _service(tmp_path)
    old = [_event(service, age) for age in (6, 5)]
    service.rag_store.upsert(MemoryDocument(
        user_id="alice", title="Legacy gym memory", content="Old gym feedback",
        tags=("llm-summary",), source_ids=tuple(event.event_id for event in old),
        occurred_at=old[-1].timestamp,
    ))
    _event(service, 1, 8, scope="persistent")
    assert service.list_long_term_summaries("alice")[0]["status"] == "review_required"


def test_missing_projection_records_expiry_without_repeated_tombstones(tmp_path):
    service = _service(tmp_path)
    preference = _old_habit(service)
    # A different aggregator window can remove the old projection; represent
    # that disappearance as an expired current version, retaining the old one.
    service.history_store.sync("alice", [], now=NOW, source_activities={})
    history = service.history_store.list_versions("alice")
    assert history[0]["status"] == "expired" and history[0]["memory_id"] == preference["memory_id"]
    service.history_store.sync("alice", [], now=NOW + timedelta(hours=1), source_activities={})
    assert service.history_store.list_versions("alice") == history


def _memory_page():
    import streamlit as st
    from frontend import render_memory_page

    render_memory_page("alice", memory_service=st.session_state["test_memory_service"])


def test_memory_page_shows_versions_and_archives_reviewed_summary(tmp_path):
    from streamlit.testing.v1 import AppTest

    service = _service(tmp_path)
    _old_habit(service)
    _summary(service)
    _event(service, 1, 8, scope="persistent")
    app = AppTest.from_function(_memory_page)
    app.session_state["test_memory_service"] = service
    app.run()
    assert not app.exception
    assert app.dataframe[0].value["Version"].tolist() == [2, 1]
    next(button for button in app.button if button.label == "Archive summary").click().run()
    assert not app.exception
    assert service.list_long_term_summaries("alice")[0]["status"] == "superseded"


def test_memory_page_rejects_a_submission_after_an_unseen_preference_change(tmp_path):
    from streamlit.testing.v1 import AppTest

    service = _service(tmp_path)
    _old_habit(service)
    _summary(service)
    _event(service, 2, 8, scope="persistent")
    app = AppTest.from_function(_memory_page)
    app.session_state["test_memory_service"] = service
    app.run()
    _event(service, 1, 7, scope="persistent")
    next(button for button in app.button if button.label == "Keep as historical context").click().run()
    assert not app.exception
    assert any("changed" in error.value for error in app.error)
    assert service.list_long_term_summaries("alice")[0]["status"] == "review_required"
