"""A local, rebuildable RAG store for personal long-term memory.

The SQLite document table is the source of truth.  Chunk vectors are stored
alongside the documents only to rank retrieval results and can always be
rebuilt.  The default embedder is deterministic and offline so the planner and
tests never send personal data to a third party.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import sqrt
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Literal, Protocol
from uuid import uuid4


DEFAULT_RAG_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "personal_rag.db"
MemoryType = Literal["episode", "preference", "goal", "fact"]
MemoryStatus = Literal["candidate", "active", "paused", "forgotten"]


class EmbeddingProvider(Protocol):
    """Replaceable embedding boundary; cloud providers are opt-in."""

    def embed(self, text: str) -> list[float]: ...


class OpenAIEmbeddingConfigurationError(RuntimeError):
    """Raised when OpenAI embeddings are selected without usable credentials."""


class HashEmbeddingProvider:
    """Deterministic local vectorizer for an offline RAG baseline.

    It combines word tokens with CJK character n-grams.  This is deliberately
    modest, but it provides stable vector retrieval without downloading a model
    or sharing personal memory.  A semantic embedding provider can replace it
    later without changing the repository or retriever contracts.
    """

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 32:
            raise ValueError("dimensions must be at least 32")
        self.dimensions = dimensions

    @property
    def signature(self) -> str:
        return f"hash:{self.dimensions}"

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += -1.0 if digest[4] & 1 else 1.0
        magnitude = sqrt(sum(value * value for value in vector))
        return [value / magnitude for value in vector] if magnitude else vector


class OpenAIEmbeddingProvider:
    """OpenAI-backed embedding provider for semantic personal-memory recall."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        client: object | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("embedding model must not be blank")
        self.model = model.strip()
        if client is None:
            clean_api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
            if not _is_configured_api_key(clean_api_key):
                raise OpenAIEmbeddingConfigurationError(
                    "OpenAI embeddings require OPENAI_API_KEY. Set it in .env or "
                    "choose RAG_EMBEDDING_PROVIDER=hash for offline retrieval."
                )
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise OpenAIEmbeddingConfigurationError(
                    "OpenAI Python SDK is required for OpenAI embeddings."
                ) from exc
            client = OpenAI(api_key=clean_api_key)
        self.client = client

    @property
    def signature(self) -> str:
        return f"openai:{self.model}"

    def embed(self, text: str) -> list[float]:
        clean_text = _required_text("embedding text", text).replace("\n", " ")
        response = self.client.embeddings.create(
            input=[clean_text],
            model=self.model,
            encoding_format="float",
        )
        try:
            embedding = [float(value) for value in response.data[0].embedding]
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("OpenAI embeddings returned an invalid vector") from exc
        return _normalize_embedding(embedding)


def configured_embedding_provider() -> EmbeddingProvider:
    """Resolve the configured provider, falling back only when no key exists.

    ``auto`` is the default for local development: an available OpenAI API key
    selects semantic embeddings; otherwise the app stays usable offline.  Set
    ``RAG_EMBEDDING_PROVIDER=openai`` to require OpenAI and fail loudly when a
    key is missing.
    """

    provider = os.getenv("RAG_EMBEDDING_PROVIDER", "auto").strip().casefold()
    model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small").strip()
    if provider == "openai":
        return OpenAIEmbeddingProvider(model=model)
    if provider == "hash":
        return HashEmbeddingProvider()
    if provider == "auto":
        return (
            OpenAIEmbeddingProvider(model=model)
            if _is_configured_api_key(os.getenv("OPENAI_API_KEY", ""))
            else HashEmbeddingProvider()
        )
    raise OpenAIEmbeddingConfigurationError(
        "RAG_EMBEDDING_PROVIDER must be one of: auto, openai, hash."
    )


@dataclass(frozen=True)
class MemoryDocument:
    """Canonical, user-scoped long-term memory record."""

    user_id: str
    title: str
    content: str
    memory_type: MemoryType = "episode"
    status: MemoryStatus = "active"
    memory_id: str = field(default_factory=lambda: str(uuid4()))
    source_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    confidence: float = 1.0
    importance: float = 0.5
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _required_text("user_id", self.user_id)
        _required_text("memory_id", self.memory_id)
        _required_text("title", self.title)
        _required_text("content", self.content)
        if self.memory_type not in {"episode", "preference", "goal", "fact"}:
            raise ValueError("unsupported memory_type")
        if self.status not in {"candidate", "active", "paused", "forgotten"}:
            raise ValueError("unsupported memory status")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        _require_aware("occurred_at", self.occurred_at)
        if self.expires_at is not None:
            _require_aware("expires_at", self.expires_at)


@dataclass(frozen=True)
class RetrievedMemory:
    """A bounded, explainable chunk returned to the planner."""

    memory_id: str
    title: str
    content: str
    memory_type: str
    score: float
    source_ids: tuple[str, ...]
    occurred_at: datetime

    def to_context(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "title": self.title,
            "content": self.content,
            "type": self.memory_type,
            "score": round(self.score, 4),
            "source_ids": list(self.source_ids),
            "occurred_at": self.occurred_at.isoformat(),
        }


class PersonalRagStore:
    """SQLite-backed canonical records plus a rebuildable local vector index."""

    def __init__(
        self,
        path: str | Path = DEFAULT_RAG_DATABASE_PATH,
        *,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self.path = Path(path)
        self.embedder = embedder or HashEmbeddingProvider()
        self._initialize()
        self._ensure_embedding_index()

    def set_profile(self, user_id: str, values: dict[str, str]) -> None:
        clean_user_id = _required_text("user_id", user_id)
        if not isinstance(values, dict) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str)
            for key, value in values.items()
        ):
            raise ValueError("profile values must be a string dictionary")
        with self._connect() as connection:
            for key, value in values.items():
                connection.execute(
                    """
                    INSERT INTO profiles (user_id, key, value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, key) DO UPDATE SET
                        value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (clean_user_id, key.strip(), value.strip(), _utc_now()),
                )

    def get_profile(self, user_id: str) -> dict[str, str]:
        clean_user_id = _required_text("user_id", user_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value FROM profiles WHERE user_id = ?", (clean_user_id,)
            ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def upsert(self, document: MemoryDocument) -> None:
        if not isinstance(document, MemoryDocument):
            raise TypeError("document must be a MemoryDocument")
        checksum = _checksum(document)
        chunks = _chunk_text(document.content)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT user_id, checksum FROM memory_documents WHERE memory_id = ?",
                (document.memory_id,),
            ).fetchone()
            if existing is not None and str(existing["user_id"]) != document.user_id:
                raise ValueError("memory_id already belongs to another user")
            connection.execute(
                """
                INSERT INTO memory_documents (
                    memory_id, user_id, memory_type, status, title, content,
                    source_ids, tags, confidence, importance, occurred_at,
                    expires_at, checksum, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    memory_type = excluded.memory_type,
                    status = excluded.status,
                    title = excluded.title,
                    content = excluded.content,
                    source_ids = excluded.source_ids,
                    tags = excluded.tags,
                    confidence = excluded.confidence,
                    importance = excluded.importance,
                    occurred_at = excluded.occurred_at,
                    expires_at = excluded.expires_at,
                    checksum = excluded.checksum,
                    updated_at = excluded.updated_at
                """,
                (
                    document.memory_id,
                    document.user_id,
                    document.memory_type,
                    document.status,
                    document.title,
                    document.content,
                    json.dumps(document.source_ids),
                    json.dumps(document.tags),
                    document.confidence,
                    document.importance,
                    document.occurred_at.astimezone(timezone.utc).isoformat(),
                    document.expires_at.astimezone(timezone.utc).isoformat()
                    if document.expires_at
                    else None,
                    checksum,
                    _utc_now(),
                ),
            )
            if existing is not None and str(existing["checksum"]) == checksum:
                return
            connection.execute("DELETE FROM memory_chunks WHERE memory_id = ?", (document.memory_id,))
            for position, content in enumerate(chunks):
                connection.execute(
                    """
                    INSERT INTO memory_chunks (chunk_id, memory_id, position, content, embedding)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        f"{document.memory_id}:{position}",
                        document.memory_id,
                        position,
                        content,
                        json.dumps(self.embedder.embed(content)),
                    ),
                )

    def set_status(self, user_id: str, memory_id: str, status: MemoryStatus) -> None:
        clean_user_id = _required_text("user_id", user_id)
        clean_memory_id = _required_text("memory_id", memory_id)
        if status not in {"candidate", "active", "paused", "forgotten"}:
            raise ValueError("unsupported memory status")
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE memory_documents SET status = ?, updated_at = ?
                WHERE memory_id = ? AND user_id = ?
                """,
                (status, _utc_now(), clean_memory_id, clean_user_id),
            )
        if result.rowcount != 1:
            raise KeyError("unknown memory")

    def forget(self, user_id: str, memory_id: str) -> None:
        """Delete the canonical document and every derived vector chunk."""

        clean_user_id = _required_text("user_id", user_id)
        clean_memory_id = _required_text("memory_id", memory_id)
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM memory_documents WHERE memory_id = ? AND user_id = ?",
                (clean_memory_id, clean_user_id),
            )
        if result.rowcount != 1:
            raise KeyError("unknown memory")

    def suppress_sources(self, user_id: str, source_ids: Iterable[str]) -> None:
        """Keep retained audit events out of future planning retrieval."""

        clean_user_id = _required_text("user_id", user_id)
        clean_source_ids = tuple(_required_text("source_id", source_id) for source_id in source_ids)
        with self._connect() as connection:
            for source_id in clean_source_ids:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO suppressed_sources (user_id, source_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (clean_user_id, source_id, _utc_now()),
                )
                connection.execute(
                    """
                    DELETE FROM memory_documents
                    WHERE user_id = ? AND source_ids LIKE ?
                    """,
                    (clean_user_id, f'%"{source_id}"%'),
                )

    def is_source_suppressed(self, user_id: str, source_id: str) -> bool:
        clean_user_id = _required_text("user_id", user_id)
        clean_source_id = _required_text("source_id", source_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM suppressed_sources
                WHERE user_id = ? AND source_id = ?
                """,
                (clean_user_id, clean_source_id),
            ).fetchone()
        return row is not None

    def mark_sources_consolidated(
        self, user_id: str, source_ids: Iterable[str]
    ) -> None:
        """Record which immutable source events an LLM summary has covered."""

        clean_user_id = _required_text("user_id", user_id)
        clean_source_ids = tuple(
            _required_text("source_id", source_id) for source_id in source_ids
        )
        with self._connect() as connection:
            for source_id in clean_source_ids:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO consolidated_sources (user_id, source_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (clean_user_id, source_id, _utc_now()),
                )

    def is_source_consolidated(self, user_id: str, source_id: str) -> bool:
        clean_user_id = _required_text("user_id", user_id)
        clean_source_id = _required_text("source_id", source_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM consolidated_sources
                WHERE user_id = ? AND source_id = ?
                """,
                (clean_user_id, clean_source_id),
            ).fetchone()
        return row is not None

    def rebuild_index(self, user_id: str | None = None) -> int:
        """Recreate every derived vector chunk from canonical documents."""

        clean_user_id = _required_text("user_id", user_id) if user_id is not None else None
        clause = "WHERE user_id = ?" if clean_user_id else ""
        parameters: tuple[str, ...] = (clean_user_id,) if clean_user_id else ()
        with self._connect() as connection:
            documents = connection.execute(
                f"""
                SELECT memory_id, content FROM memory_documents {clause}
                """,
                parameters,
            ).fetchall()
            if clean_user_id:
                connection.execute(
                    """
                    DELETE FROM memory_chunks
                    WHERE memory_id IN (
                        SELECT memory_id FROM memory_documents WHERE user_id = ?
                    )
                    """,
                    (clean_user_id,),
                )
            else:
                connection.execute("DELETE FROM memory_chunks")
            for document in documents:
                memory_id = str(document["memory_id"])
                for position, content in enumerate(_chunk_text(str(document["content"]))):
                    connection.execute(
                        """
                        INSERT INTO memory_chunks (chunk_id, memory_id, position, content, embedding)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            f"{memory_id}:{position}",
                            memory_id,
                            position,
                            content,
                            json.dumps(self.embedder.embed(content)),
                        ),
                    )
        self._set_index_signature()
        return len(documents)

    def retrieve(
        self,
        user_id: str,
        query: str,
        *,
        limit: int = 6,
        memory_types: Iterable[MemoryType] | None = None,
        now: datetime | None = None,
    ) -> list[RetrievedMemory]:
        clean_user_id = _required_text("user_id", user_id)
        clean_query = _required_text("query", query)
        if limit < 1:
            raise ValueError("limit must be positive")
        clean_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        type_filter = tuple(memory_types or ())
        if any(item not in {"episode", "preference", "goal", "fact"} for item in type_filter):
            raise ValueError("unsupported memory type filter")

        clauses = ["d.user_id = ?", "d.status = 'active'", "(d.expires_at IS NULL OR d.expires_at > ?)"]
        parameters: list[object] = [clean_user_id, clean_now.isoformat()]
        if type_filter:
            clauses.append(f"d.memory_type IN ({','.join('?' for _ in type_filter)})")
            parameters.extend(type_filter)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT d.memory_id, d.title, d.memory_type, d.source_ids,
                       d.confidence, d.importance, d.occurred_at,
                       c.content, c.embedding
                FROM memory_documents d
                JOIN memory_chunks c ON c.memory_id = d.memory_id
                WHERE {' AND '.join(clauses)}
                """,
                parameters,
            ).fetchall()

        query_vector = self.embedder.embed(clean_query)
        query_tokens = set(_tokens(clean_query))
        ranked: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            vector_score = _cosine(query_vector, json.loads(str(row["embedding"])))
            lexical_score = _lexical_overlap(query_tokens, set(_tokens(str(row["content"]))))
            recency_score = _recency(datetime.fromisoformat(str(row["occurred_at"])), clean_now)
            score = (
                0.50 * max(0.0, vector_score)
                + 0.25 * lexical_score
                + 0.10 * recency_score
                + 0.10 * float(row["confidence"])
                + 0.05 * float(row["importance"])
            )
            if score > 0:
                ranked.append((score, row))

        seen: set[str] = set()
        memories: list[RetrievedMemory] = []
        for score, row in sorted(ranked, key=lambda item: item[0], reverse=True):
            memory_id = str(row["memory_id"])
            if memory_id in seen:
                continue
            seen.add(memory_id)
            memories.append(
                RetrievedMemory(
                    memory_id=memory_id,
                    title=str(row["title"]),
                    content=str(row["content"]),
                    memory_type=str(row["memory_type"]),
                    score=score,
                    source_ids=tuple(json.loads(str(row["source_ids"]))),
                    occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
                )
            )
            if len(memories) == limit:
                break
        return memories

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, key)
                );
                CREATE TABLE IF NOT EXISTS memory_documents (
                    memory_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_ids TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    occurred_at TEXT NOT NULL,
                    expires_at TEXT,
                    checksum TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_documents_user_status
                    ON memory_documents (user_id, status, memory_type);
                CREATE TABLE IF NOT EXISTS memory_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL REFERENCES memory_documents(memory_id)
                        ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_chunks_document
                    ON memory_chunks (memory_id, position);
                CREATE TABLE IF NOT EXISTS suppressed_sources (
                    user_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS consolidated_sources (
                    user_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS rag_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def _ensure_embedding_index(self) -> None:
        """Rebuild derived vectors after an embedding model/provider change."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM rag_metadata WHERE key = 'embedding_signature'"
            ).fetchone()
            document_count = int(
                connection.execute("SELECT COUNT(*) FROM memory_documents").fetchone()[0]
            )
        if row is None or str(row["value"]) != self._embedding_signature():
            if document_count:
                self.rebuild_index()
            else:
                self._set_index_signature()

    def _set_index_signature(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO rag_metadata (key, value) VALUES ('embedding_signature', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (self._embedding_signature(),),
            )

    def _embedding_signature(self) -> str:
        return str(
            getattr(
                self.embedder,
                "signature",
                f"{type(self.embedder).__module__}.{type(self.embedder).__qualname__}",
            )
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _chunk_text(content: str, *, max_words: int = 180) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs or [content.strip()]:
        words = paragraph.split()
        if len(words) <= max_words:
            chunks.append(paragraph)
            continue
        for start in range(0, len(words), max_words):
            chunks.append(" ".join(words[start : start + max_words]))
    return chunks


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    cjk_bigrams = ["".join(cjk[index : index + 2]) for index in range(len(cjk) - 1)]
    return words + cjk + cjk_bigrams


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    return sum(first * second for first, second in zip(left, right, strict=True))


def _normalize_embedding(vector: list[float]) -> list[float]:
    magnitude = sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        raise RuntimeError("embedding vector must not be all zero")
    return [value / magnitude for value in vector]


def _lexical_overlap(query_tokens: set[str], chunk_tokens: set[str]) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    return len(query_tokens & chunk_tokens) / len(query_tokens)


def _recency(occurred_at: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - occurred_at.astimezone(timezone.utc)).total_seconds() / 86400)
    return 1.0 / (1.0 + age_days / 180.0)


def _checksum(document: MemoryDocument) -> str:
    payload = "\x1f".join(
        (document.title, document.content, document.status, str(document.confidence), str(document.importance))
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()


def _is_configured_api_key(value: str) -> bool:
    clean_value = value.strip()
    return bool(clean_value) and not clean_value.startswith("replace_with_")


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
