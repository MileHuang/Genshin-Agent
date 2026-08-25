"""用户偏好工具：合并固定资料与从行为反馈学习到的偏好。"""

from __future__ import annotations

from pathlib import Path

from tools.edit_event import EditEventStore
from tools.preference_aggregator import PreferenceAggregator


TOOL_NAME = "get_user_preferences"

TOOL_DESCRIPTION = """
获取用户的生活习惯和偏好信息。
包括作息时间、运动习惯、专注时间。
"""


DEFAULT_BEHAVIOR_HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "behavior_history.jsonl"
)


def get_user_preferences(
    *,
    user_id: str = "default",
    event_store: EditEventStore | None = None,
    aggregator: PreferenceAggregator | None = None,
) -> dict:
    """
    获取用户偏好。

    固定资料保持 V1 原有行为；学习到的偏好由本地行为历史即时聚合。
    尚未有历史记录时，``学习到的偏好`` 返回空列表，Planner 可以继续
    按固定资料正常工作。

    Returns:
        dict:
        用户生活习惯信息
    """

    store = event_store or EditEventStore(DEFAULT_BEHAVIOR_HISTORY_PATH)
    preference_aggregator = aggregator or PreferenceAggregator()
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id 不能为空")
    clean_user_id = user_id.strip()
    learned_preferences = [
        preference.to_dict()
        for preference in preference_aggregator.aggregate(
            store.list_events(user_id=clean_user_id)
        )
    ]

    return {
        "作息": {
            "起床时间": "08:00",
            "睡觉时间": "23:00"
        },

        "运动习惯": "晚上运动",

        "专注时间": "上午",

        "学习到的偏好": learned_preferences,
    }
