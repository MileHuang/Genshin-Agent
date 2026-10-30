"""Durable preference versions and conservative, source-linked summary review."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from tools.behavior_preference import MIN_SCHEDULING_CONFIDENCE


class PreferenceHistoryStore:
    """Keep observed preference revisions in the personal RAG database.

    Repeated reads are idempotent. Every changed snapshot closes its previous
    validity interval and records the replacement. Historical versions never
    participate in vector retrieval or the planner's preferences projection.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS preference_versions (
                    version_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    snapshot TEXT NOT NULL,
                    status TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    replaces TEXT REFERENCES preference_versions(version_id),
                    UNIQUE(user_id, memory_id, version)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_current_preference_version
                ON preference_versions(user_id, memory_id) WHERE valid_to IS NULL;
            """)

    def sync(self, user_id: str, preferences: list[dict], *, now: datetime,
             source_activities: dict[str, str], forgotten_ids: set[str] | None = None) -> None:
        timestamp = now.astimezone(timezone.utc).isoformat()
        current = {item["memory_id"]: self._snapshot(item) for item in preferences}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = {row["memory_id"]: row for row in connection.execute(
                "SELECT * FROM preference_versions WHERE user_id = ? AND valid_to IS NULL",
                (user_id,),
            )}
            for memory_id in sorted(current.keys() | previous.keys()):
                old = previous.get(memory_id)
                before = json.loads(old["snapshot"]) if old is not None else None
                snapshot = current.get(memory_id)
                if snapshot is None:
                    snapshot = dict(before)
                    snapshot["status"] = "forgotten" if memory_id in (forgotten_ids or set()) else "expired"
                if snapshot == before:
                    continue
                if old is not None:
                    connection.execute(
                        "UPDATE preference_versions SET status = 'superseded', valid_to = ? WHERE version_id = ?",
                        (timestamp, old["version_id"]),
                    )
                version_id = str(uuid4())
                connection.execute(
                    "INSERT INTO preference_versions VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    (version_id, user_id, memory_id, old["version"] + 1 if old else 1,
                     json.dumps(snapshot, sort_keys=True), snapshot["status"], timestamp,
                     old["version_id"] if old else None),
                )
                changed_meaning = before is not None and (
                    snapshot["value"] != before["value"]
                    or (before["status"] == "active" and snapshot["status"] != "active")
                )
                if changed_meaning or (before is None and snapshot["status"] == "active"):
                    self._queue_summaries(connection, user_id, snapshot, before,
                                          source_activities, timestamp, version_id)

    @staticmethod
    def _snapshot(preference: dict) -> dict:
        status = preference.get("status", "active")
        if status == "active" and preference["confidence"] < MIN_SCHEDULING_CONFIDENCE:
            status = "candidate"
        return {
            "memory_id": preference["memory_id"],
            "activity_type": preference["activity_type"],
            "attribute": preference["attribute"],
            "value": preference["value"],
            "confidence": preference["confidence"],
            "evidence_event_ids": preference["evidence_event_ids"],
            "last_supported_at": preference["updated_at"],
            "status": status,
        }

    @staticmethod
    def _queue_summaries(connection: sqlite3.Connection, user_id: str, snapshot: dict,
                         before: dict | None, activities: dict[str, str],
                         timestamp: str, version_id: str) -> None:
        rows = connection.execute(
            "SELECT * FROM memory_documents WHERE user_id = ? AND status IN ('active', 'review_required')",
            (user_id,),
        ).fetchall()
        for row in rows:
            if "llm-summary" not in json.loads(row["tags"]):
                continue
            sources = set(json.loads(row["source_ids"]))
            related = {source for source in sources if activities.get(source) == snapshot["activity_type"]}
            if not related:
                continue
            if before is None:
                # Bootstrap existing databases conservatively: only flag old,
                # disjoint evidence, not summaries supporting the current habit.
                if related & set(snapshot["evidence_event_ids"]):
                    continue
                if datetime.fromisoformat(row["occurred_at"]) >= datetime.fromisoformat(snapshot["last_supported_at"]):
                    continue
            reason = (
                f"Preference {snapshot['activity_type']} / {snapshot['attribute']} changed "
                f"(version {version_id}); review this historical summary before reuse."
            )
            connection.execute(
                """UPDATE memory_documents SET status = 'review_required', review_reason = ?, review_token = ?,
                   checksum = '', updated_at = ? WHERE memory_id = ?""",
                (reason, version_id, timestamp, row["memory_id"]),
            )
            connection.execute("DELETE FROM memory_chunks WHERE memory_id = ?", (row["memory_id"],))

    def list_versions(self, user_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM preference_versions WHERE user_id = ? ORDER BY memory_id, version DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) | {"snapshot": json.loads(row["snapshot"])} for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
