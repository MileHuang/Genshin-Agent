from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from memory.rag import (
    ConsolidatedMemory,
    HashEmbeddingProvider,
    MemoryDocument,
    OpenAIEmbeddingConfigurationError,
    OpenAIEmbeddingProvider,
    PersonalRagStore,
    configured_embedding_provider,
)
from memory.rag.consolidator import KimiMemorySummarizer
from memory.session_memory import SessionMemoryStore
from tools.edit_event import EditEventStore
from tools.feedback_service import FeedbackService
from tools.memory_context import (
    LocalMemoryContextService,
    MemoryControlStore,
    RagMemoryContextService,
)


def _moment(day: int = 1) -> datetime:
    return datetime(2026, 9, day, 12, tzinfo=timezone.utc)


def test_personal_rag_retrieves_only_the_requesting_users_memory(tmp_path):
    store = PersonalRagStore(tmp_path / "personal_rag.db")
    store.upsert(
        MemoryDocument(
            memory_id="alice-evening-run",
            user_id="alice",
            title="Evening running preference",
            content="Alice usually moves running to the evening after work.",
            tags=("running", "evening"),
            occurred_at=_moment(),
        )
    )
    store.upsert(
        MemoryDocument(
            memory_id="bob-secret",
            user_id="bob",
            title="Private health note",
            content="Bob has a private health constraint.",
            occurred_at=_moment(),
        )
    )

    results = store.retrieve("alice", "Plan an evening run", now=_moment(2))

    assert [result.memory_id for result in results] == ["alice-evening-run"]
    assert "Bob" not in results[0].content


def test_forgetting_removes_canonical_memory_and_derived_chunks(tmp_path):
    store = PersonalRagStore(tmp_path / "personal_rag.db")
    store.upsert(
        MemoryDocument(
            memory_id="forget-me",
            user_id="alice",
            title="Temporary memory",
            content="A personal memory that must be erased.",
            occurred_at=_moment(),
        )
    )

    store.forget("alice", "forget-me")

    assert store.retrieve("alice", "personal memory", now=_moment(2)) == []
    with pytest.raises(KeyError):
        store.forget("alice", "forget-me")


def test_vector_index_can_be_rebuilt_from_canonical_documents(tmp_path):
    store = PersonalRagStore(tmp_path / "personal_rag.db")
    store.upsert(
        MemoryDocument(
            memory_id="rebuild-me",
            user_id="alice",
            title="Reading preference",
            content="Alice enjoys reading in quiet cafes.",
            occurred_at=_moment(),
        )
    )

    assert store.rebuild_index("alice") == 1
    assert store.retrieve("alice", "quiet reading", now=_moment(2))[0].memory_id == "rebuild-me"


def test_openai_embedding_provider_uses_official_sdk_embedding_call_shape():
    calls: list[dict] = []

    class FakeEmbeddings:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[3.0, 4.0])]
            )

    provider = OpenAIEmbeddingProvider(
        model="text-embedding-3-small",
        client=SimpleNamespace(embeddings=FakeEmbeddings()),
    )

    assert provider.embed("First line\nsecond line") == [0.6, 0.8]
    assert calls == [
        {
            "input": ["First line second line"],
            "model": "text-embedding-3-small",
            "encoding_format": "float",
        }
    ]


def test_embedding_provider_auto_falls_back_without_key_and_openai_can_be_required(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "auto")
    assert isinstance(configured_embedding_provider(), HashEmbeddingProvider)

    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "openai")
    with pytest.raises(OpenAIEmbeddingConfigurationError, match="OPENAI_API_KEY"):
        configured_embedding_provider()


def test_embedding_provider_change_rebuilds_derived_vectors(tmp_path):
    path = tmp_path / "personal_rag.db"
    first = PersonalRagStore(path, embedder=HashEmbeddingProvider(dimensions=64))
    first.upsert(
        MemoryDocument(
            memory_id="switch-model",
            user_id="alice",
            title="Dinner planning",
            content="Alice prefers dinner after a long walk.",
            occurred_at=_moment(),
        )
    )

    second = PersonalRagStore(path, embedder=HashEmbeddingProvider(dimensions=32))

    assert second.retrieve("alice", "long walk dinner", now=_moment(2))[0].memory_id == "switch-model"


def test_session_markdown_is_temporary_and_not_long_term_rag(tmp_path):
    sessions = SessionMemoryStore(tmp_path / "sessions")
    saved = sessions.save(
        user_id="alice",
        session_id="today",
        timezone_name="America/New_York",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        goal="Plan a calm evening",
        temporary_constraints=["No meetings after 18:00"],
    )

    loaded = sessions.load_latest_active("alice")

    assert loaded == saved
    assert "No meetings after 18:00" in loaded.body


def test_rag_context_syncs_feedback_and_keeps_profile_and_session_separate(tmp_path):
    events = EditEventStore(tmp_path / "events.jsonl")
    feedback = FeedbackService(events)
    feedback.move(
        activity_type="gym",
        original_start=_moment(1),
        original_end=_moment(1) + timedelta(hours=1),
        new_start=_moment(1).replace(hour=20),
        new_end=_moment(1).replace(hour=21),
        source_plan_id="plan-1",
        user_id="alice",
    )
    base = LocalMemoryContextService(
        event_store=events,
        control_store=MemoryControlStore(tmp_path / "controls.json"),
    )
    service = RagMemoryContextService(
        base_service=base,
        rag_store=PersonalRagStore(tmp_path / "personal_rag.db"),
        session_store=SessionMemoryStore(tmp_path / "sessions"),
    )
    service.update_profile_preference("alice", "timezone", "America/New_York")
    service.begin_session("alice", "daily", "Plan a gym session")

    context = service.get_memory_context("alice", "Plan a gym session")

    assert context["profile"]["timezone"] == "America/New_York"
    assert context["working_memory"]["session_id"] == "daily"
    assert context["relevant_memories"][0]["memory_id"].startswith("edit-event:")
    assert context["relevant_memories"][0]["source_ids"]


def test_forgotten_preference_evidence_is_not_reintroduced_by_rag(tmp_path):
    events = EditEventStore(tmp_path / "events.jsonl")
    feedback = FeedbackService(events)
    for day in (1, 2, 3):
        feedback.move(
            activity_type="gym",
            original_start=_moment(day),
            original_end=_moment(day) + timedelta(hours=1),
            new_start=_moment(day).replace(hour=20),
            new_end=_moment(day).replace(hour=21),
            source_plan_id=f"plan-{day}",
            user_id="alice",
        )
    service = RagMemoryContextService(
        base_service=LocalMemoryContextService(
            event_store=events,
            control_store=MemoryControlStore(tmp_path / "controls.json"),
        ),
        rag_store=PersonalRagStore(tmp_path / "personal_rag.db"),
        session_store=SessionMemoryStore(tmp_path / "sessions"),
    )
    preference_id = service.get_memory_context("alice", "gym")["preferences"][0]["memory_id"]

    service.forget_memory("alice", preference_id)
    context = service.get_memory_context("alice", "Plan a gym session")

    assert context["preferences"] == []
    assert context["relevant_events"] == []
    assert context["relevant_memories"] == []


def test_kimi_summarizer_requires_json_with_only_factual_memory_fields(tmp_path):
    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["response_format"] == {"type": "json_object"}
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"title":"Evening gym changes",'
                                '"summary":"The user repeatedly moved gym later.",'
                                '"importance":0.7}'
                            )
                        )
                    )
                ]
            )

    summarizer = KimiMemorySummarizer(
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    )
    event = FeedbackService(EditEventStore(tmp_path / "events.jsonl")).accept(
        activity_type="gym",
        original_start=_moment(),
        original_end=_moment() + timedelta(hours=1),
        source_plan_id="plan-1",
        user_id="alice",
    )

    result = summarizer.summarize("alice", [event])

    assert result.title == "Evening gym changes"
    assert result.importance == 0.7


def test_llm_consolidation_replaces_raw_retrieval_with_source_linked_summary(tmp_path):
    events = EditEventStore(tmp_path / "events.jsonl")
    feedback = FeedbackService(events)
    for day in (1, 2):
        feedback.move(
            activity_type="gym",
            original_start=_moment(day),
            original_end=_moment(day) + timedelta(hours=1),
            new_start=_moment(day).replace(hour=20),
            new_end=_moment(day).replace(hour=21),
            source_plan_id=f"plan-{day}",
            user_id="alice",
        )

    class FakeSummarizer:
        def summarize(self, user_id, source_events):
            assert user_id == "alice"
            assert len(source_events) == 2
            return ConsolidatedMemory(
                title="Gym moved later",
                summary="The user moved two gym sessions from noon to the evening.",
                importance=0.8,
            )

    service = RagMemoryContextService(
        base_service=LocalMemoryContextService(
            event_store=events,
            control_store=MemoryControlStore(tmp_path / "controls.json"),
        ),
        rag_store=PersonalRagStore(tmp_path / "personal_rag.db"),
        session_store=SessionMemoryStore(tmp_path / "sessions"),
        summarizer=FakeSummarizer(),
    )

    preview = service.preview_feedback_consolidation("alice")
    before_confirmation = service.get_memory_context("alice", "Plan a gym session")

    assert preview is not None
    assert preview.summary.title == "Gym moved later"
    assert len(before_confirmation["relevant_events"]) == 2

    summary = service.confirm_feedback_consolidation(
        "alice",
        preview,
        content="The user moved two gym sessions from noon to later evening slots.",
    )
    context = service.get_memory_context("alice", "Plan a gym session")

    assert summary["memory_id"].startswith("llm-summary:")
    assert len(summary["source_ids"]) == 2
    assert summary["content"].endswith("later evening slots.")
    assert context["relevant_events"] == []
    assert context["relevant_memories"][0]["memory_id"] == summary["memory_id"]
