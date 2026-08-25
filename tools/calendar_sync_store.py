"""Persistent records for plans that have been created in Google Calendar."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from config.settings import GOOGLE_SYNC_RECORD_FILE


def build_plan_sync_key(target_date: str, items: Sequence[dict[str, str]]) -> str:
    """Create a deterministic ID for the exact schedule sent to Google."""

    payload = {
        "date": target_date,
        "items": [
            {
                "title": item["title"],
                "start_time": item["start_time"],
                "end_time": item["end_time"],
                "notes": item.get("notes", ""),
            }
            for item in items
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CalendarSyncStore:
    """Small JSON store that prevents the same plan being written twice."""

    def __init__(self, path: str | Path = GOOGLE_SYNC_RECORD_FILE) -> None:
        self.path = Path(path)

    def get(self, sync_key: str) -> dict[str, Any] | None:
        return self._load().get(sync_key)

    def is_synced(self, sync_key: str) -> bool:
        return self.get(sync_key) is not None

    def record(
        self,
        sync_key: str,
        *,
        target_date: str,
        event_ids: Sequence[str],
    ) -> None:
        if not sync_key:
            raise ValueError("sync_key 不能为空")
        if not event_ids:
            raise ValueError("至少需要一个 Google Calendar event ID")

        records = self._load()
        records[sync_key] = {
            "target_date": target_date,
            "event_ids": list(event_ids),
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write(records)

    def replace(
        self,
        old_sync_key: str,
        new_sync_key: str,
        *,
        target_date: str,
        event_ids: Sequence[str],
    ) -> None:
        """Move a record to the plan key produced after a local edit."""

        records = self._load()
        if old_sync_key not in records:
            raise ValueError("找不到需要更新的 Google Calendar 同步记录")
        del records[old_sync_key]
        if event_ids:
            records[new_sync_key] = {
                "target_date": target_date,
                "event_ids": list(event_ids),
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }
        self._write(records)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("无法读取 Google Calendar 同步记录") from exc
        if not isinstance(payload, dict):
            raise ValueError("Google Calendar 同步记录格式无效")
        return payload

    def _write(self, records: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)
