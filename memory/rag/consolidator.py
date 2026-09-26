"""LLM-assisted consolidation of raw feedback into retrievable memory summaries."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Protocol, Sequence

from config.settings import (
    KIMI_BASE_URL,
    KIMI_MODEL,
    KIMI_TIMEOUT_SECONDS,
    MOONSHOT_API_KEY,
)
from tools.edit_event import EditEvent


class MemoryConsolidationError(RuntimeError):
    """Raised when a memory summary cannot be created safely."""


@dataclass(frozen=True)
class ConsolidatedMemory:
    """Validated LLM output before it becomes a source-linked RAG document."""

    title: str
    summary: str
    importance: float = 0.5

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("summary title must not be blank")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("summary must not be blank")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("summary importance must be between 0 and 1")


@dataclass(frozen=True)
class ConsolidationPreview:
    """An unsaved, user-reviewable LLM summary and its immutable evidence."""

    user_id: str
    source_ids: tuple[str, ...]
    summary: ConsolidatedMemory

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise ValueError("preview user_id must not be blank")
        if len(self.source_ids) < 2 or any(not source_id for source_id in self.source_ids):
            raise ValueError("a preview needs at least two source event IDs")


class MemorySummarizer(Protocol):
    """Boundary that keeps memory consolidation testable without a live LLM."""

    def summarize(
        self, user_id: str, events: Sequence[EditEvent]
    ) -> ConsolidatedMemory: ...


class KimiMemorySummarizer:
    """Use Kimi to compress feedback into an episodic summary, never a rule."""

    def __init__(self, *, client: Any | None = None) -> None:
        if client is None:
            if not MOONSHOT_API_KEY:
                raise MemoryConsolidationError(
                    "Kimi memory consolidation requires MOONSHOT_API_KEY."
                )
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise MemoryConsolidationError(
                    "OpenAI Python SDK is required for Kimi memory consolidation."
                ) from exc
            client = OpenAI(
                api_key=MOONSHOT_API_KEY,
                base_url=KIMI_BASE_URL,
                timeout=KIMI_TIMEOUT_SECONDS,
            )
        self.client = client

    def summarize(
        self, user_id: str, events: Sequence[EditEvent]
    ) -> ConsolidatedMemory:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must not be blank")
        if not events:
            raise ValueError("at least one event is required for consolidation")

        try:
            response = self.client.chat.completions.create(
                model=KIMI_MODEL,
                # Kimi K3 currently accepts only its default temperature.
                # Schema validation below still rejects unusable output.
                temperature=1,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Consolidate personal planning feedback into one concise, "
                            "factual episodic-memory summary. Do not invent facts, state "
                            "a permanent preference, or give planning advice. Return exactly "
                            "JSON with title, summary, and importance from 0 to 1."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "user_id": user_id.strip(),
                                "feedback_events": [event.to_dict() for event in events],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )
        except Exception as exc:
            raise MemoryConsolidationError(
                "Kimi could not consolidate the selected feedback."
            ) from exc
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError) as exc:
            raise MemoryConsolidationError("Kimi returned no memory summary") from exc
        return _parse_summary(content)


def _parse_summary(content: object) -> ConsolidatedMemory:
    if not isinstance(content, str) or not content.strip():
        raise MemoryConsolidationError("Kimi returned an empty memory summary")
    clean_content = content.strip()
    try:
        payload = json.loads(clean_content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean_content, flags=re.DOTALL)
        if match is None:
            raise MemoryConsolidationError("Kimi did not return JSON memory output") from None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise MemoryConsolidationError("Kimi returned invalid JSON memory output") from exc
    if not isinstance(payload, dict):
        raise MemoryConsolidationError("Kimi memory output must be an object")
    try:
        return ConsolidatedMemory(
            title=str(payload["title"]).strip(),
            summary=str(payload["summary"]).strip(),
            importance=float(payload.get("importance", 0.5)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MemoryConsolidationError("Kimi memory output has an invalid schema") from exc
