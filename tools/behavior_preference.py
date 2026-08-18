"""由行为事件归纳出的结构化用户偏好。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class BehaviorPreference:
    """Planner 可以直接消费的一条行为偏好。

    时间类偏好使用 ``{"start": "HH:MM", "end": "HH:MM"}``；活动
    优先级类偏好使用简短的结构化值，例如 ``{"level": "low"}``。
    """

    category: str
    activity_type: str
    attribute: str
    value: dict[str, str]
    confidence: float
    evidence_count: int
    user_id: str = "default"
    preference_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.category = _required_text("category", self.category)
        self.activity_type = _required_text("activity_type", self.activity_type)
        self.attribute = _required_text("attribute", self.attribute)
        self.user_id = _required_text("user_id", self.user_id)
        self.preference_id = _required_text("preference_id", self.preference_id)
        if not isinstance(self.evidence_count, int) or self.evidence_count < 1:
            raise ValueError("evidence_count 必须是正整数")
        if not isinstance(self.confidence, (int, float)) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")
        self.confidence = float(self.confidence)
        if self.attribute in {"preferred_time_range", "avoid_time_range"}:
            _validate_time_range(self.value)
        else:
            _validate_structured_value(self.value)
        if not isinstance(self.created_at, datetime) or not isinstance(self.updated_at, datetime):
            raise ValueError("created_at 和 updated_at 必须是 datetime")

    def to_dict(self) -> dict[str, Any]:
        """转换成可保存或传递给 Planner 的 JSON 结构。"""

        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")
    return value.strip()


def _validate_time_range(value: dict[str, str]) -> None:
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError('value 必须是包含 "start" 和 "end" 的字典')
    parsed_minutes: dict[str, int] = {}
    for name in ("start", "end"):
        time = value[name]
        if not isinstance(time, str) or len(time) != 5 or time[2] != ":":
            raise ValueError(f"value.{name} 必须是 HH:MM 格式")
        try:
            hours, minutes = (int(part) for part in time.split(":"))
        except ValueError as exc:
            raise ValueError(f"value.{name} 必须是 HH:MM 格式") from exc
        if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
            raise ValueError(f"value.{name} 必须是有效时间")
        parsed_minutes[name] = hours * 60 + minutes
    if parsed_minutes["start"] >= parsed_minutes["end"]:
        raise ValueError("value.end 必须晚于 value.start")


def _validate_structured_value(value: dict[str, str]) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError("value 必须是非空字典")
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise ValueError("value 的键和值必须是字符串")
