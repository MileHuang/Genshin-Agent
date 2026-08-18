"""用户对计划的反馈事件，以及 V1 的本地事件存储。

EditEvent 只记录事实，不在这里推断用户偏好。后续的
PreferenceAggregator 会读取这些事件来生成 BehaviorPreference。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


class EditAction(str, Enum):
    """V1 支持的四类计划反馈。"""

    ACCEPT = "ACCEPT"
    MOVE = "MOVE"
    DELETE = "DELETE"
    SKIP = "SKIP"


@dataclass
class EditEvent:
    """一次用户对某个计划项的反馈。

    所有动作都必须保留原始时间，确保后续可以判断用户是否经常
    调整或跳过同一类活动。只有 MOVE 可以带有新时间。
    """

    action: EditAction | str
    activity_type: str
    original_start: datetime
    original_end: datetime
    source_plan_id: str
    user_id: str = "default"
    new_start: datetime | None = None
    new_end: datetime | None = None
    reason: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.action = EditAction(self.action)
        self.activity_type = _required_text("activity_type", self.activity_type)
        self.source_plan_id = _required_text("source_plan_id", self.source_plan_id)
        self.user_id = _required_text("user_id", self.user_id)
        self.event_id = _required_text("event_id", self.event_id)
        self.reason = _optional_text(self.reason)
        self.original_start = _require_datetime("original_start", self.original_start)
        self.original_end = _require_datetime("original_end", self.original_end)
        self.timestamp = _require_datetime("timestamp", self.timestamp)

        if self.original_start >= self.original_end:
            raise ValueError("original_end 必须晚于 original_start")

        if self.action is EditAction.MOVE:
            self.new_start = _require_datetime("new_start", self.new_start)
            self.new_end = _require_datetime("new_end", self.new_end)
            if self.new_start >= self.new_end:
                raise ValueError("new_end 必须晚于 new_start")
            if (
                self.new_start == self.original_start
                and self.new_end == self.original_end
            ):
                raise ValueError("MOVE 事件必须改变时间段")
        elif self.new_start is not None or self.new_end is not None:
            raise ValueError("只有 MOVE 事件可以包含 new_start 和 new_end")

    def to_dict(self) -> dict[str, Any]:
        """转换成可以保存为 JSON 的字典。"""

        data = asdict(self)
        data["action"] = self.action.value
        for name in ("original_start", "original_end", "new_start", "new_end", "timestamp"):
            value = data[name]
            data[name] = value.isoformat() if value is not None else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditEvent":
        """从 ``to_dict`` 产生的字典恢复事件。"""

        parsed = dict(data)
        for name in ("original_start", "original_end", "new_start", "new_end", "timestamp"):
            if parsed.get(name) is not None:
                parsed[name] = datetime.fromisoformat(parsed[name])
        return cls(**parsed)


class EditEventStore:
    """追加式 JSON Lines 事件存储。

    V1 选用 JSONL，便于本地演示和调试；后续替换成数据库时，保持
    ``append`` 与 ``list_events`` 接口即可。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: EditEvent) -> None:
        if not isinstance(event, EditEvent):
            raise TypeError("event 必须是 EditEvent")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def list_events(
        self,
        *,
        user_id: str | None = None,
        activity_type: str | None = None,
        action: EditAction | str | None = None,
    ) -> list[EditEvent]:
        """按可选条件读取事件，顺序与写入顺序一致。"""

        if not self.path.exists():
            return []

        action_filter = EditAction(action) if action is not None else None
        events: list[EditEvent] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = EditEvent.from_dict(json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"无法读取第 {line_number} 条 EditEvent") from exc

            if user_id is not None and event.user_id != user_id:
                continue
            if activity_type is not None and event.activity_type != activity_type:
                continue
            if action_filter is not None and event.action is not action_filter:
                continue
            events.append(event)
        return events


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("reason 必须是字符串或 None")
    return value.strip() or None


def _require_datetime(name: str, value: datetime | None) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} 必须是 datetime")
    return value
