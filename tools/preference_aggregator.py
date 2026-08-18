"""将重复的 EditEvent 聚合为 BehaviorPreference。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from tools.behavior_preference import BehaviorPreference
from tools.edit_event import EditAction, EditEvent


class PreferenceAggregator:
    """V1 偏好聚合器。

    为避免把一次临时调整误判为长期习惯，所有规则都需要达到
    minimum_evidence 条事件才产生偏好。
    """

    def __init__(self, minimum_evidence: int = 3) -> None:
        if not isinstance(minimum_evidence, int) or minimum_evidence < 1:
            raise ValueError("minimum_evidence 必须是正整数")
        self.minimum_evidence = minimum_evidence

    def aggregate(self, events: Iterable[EditEvent]) -> list[BehaviorPreference]:
        """从事件流中产生正向时间、避开时间和活动优先级偏好。"""

        positive_events: dict[tuple[str, str], list[EditEvent]] = defaultdict(list)
        skipped_events: dict[tuple[str, str], list[EditEvent]] = defaultdict(list)
        deleted_events: dict[tuple[str, str], list[EditEvent]] = defaultdict(list)
        for event in events:
            if not isinstance(event, EditEvent):
                raise TypeError("events 中的每一项都必须是 EditEvent")
            key = (event.user_id, event.activity_type)
            if event.action in {EditAction.MOVE, EditAction.ACCEPT}:
                positive_events[key].append(event)
            elif event.action is EditAction.SKIP:
                skipped_events[key].append(event)
            elif event.action is EditAction.DELETE:
                deleted_events[key].append(event)

        preferences: list[BehaviorPreference] = []
        for (user_id, activity_type), activity_events in sorted(positive_events.items()):
            if len(activity_events) < self.minimum_evidence:
                continue
            preferences.append(
                self._time_range_preference(
                    user_id,
                    activity_type,
                    activity_events,
                    attribute="preferred_time_range",
                )
            )
        for (user_id, activity_type), activity_events in sorted(skipped_events.items()):
            if len(activity_events) < self.minimum_evidence:
                continue
            preferences.append(
                self._time_range_preference(
                    user_id,
                    activity_type,
                    activity_events,
                    attribute="avoid_time_range",
                )
            )
        for (user_id, activity_type), activity_events in sorted(deleted_events.items()):
            if len(activity_events) < self.minimum_evidence:
                continue
            preferences.append(
                BehaviorPreference(
                    category="planning",
                    activity_type=activity_type,
                    attribute="deprioritize_activity",
                    value={"level": "low"},
                    confidence=_count_confidence(len(activity_events), self.minimum_evidence),
                    evidence_count=len(activity_events),
                    user_id=user_id,
                )
            )
        return preferences

    def _time_range_preference(
        self,
        user_id: str,
        activity_type: str,
        activity_events: list[EditEvent],
        *,
        attribute: str,
    ) -> BehaviorPreference:
        start_minutes = [_feedback_start_minutes(event) for event in activity_events]
        end_minutes = [_feedback_end_minutes(event) for event in activity_events]
        preferred_start = round(sum(start_minutes) / len(start_minutes))
        preferred_end = round(sum(end_minutes) / len(end_minutes))

        return BehaviorPreference(
            category="scheduling",
            activity_type=activity_type,
            attribute=attribute,
            value={
                "start": _format_minutes(preferred_start),
                "end": _format_minutes(preferred_end),
            },
            confidence=_confidence(start_minutes, self.minimum_evidence),
            evidence_count=len(activity_events),
            user_id=user_id,
        )


def _feedback_start_minutes(event: EditEvent) -> int:
    value = event.new_start if event.action is EditAction.MOVE else event.original_start
    return value.hour * 60 + value.minute


def _feedback_end_minutes(event: EditEvent) -> int:
    value = event.new_end if event.action is EditAction.MOVE else event.original_end
    return value.hour * 60 + value.minute


def _format_minutes(value: int) -> str:
    value = value % (24 * 60)
    return f"{value // 60:02d}:{value % 60:02d}"


def _confidence(start_minutes: list[int], minimum_evidence: int) -> float:
    """按证据数量和时间一致性生成保守的 V1 置信度。"""

    average = sum(start_minutes) / len(start_minutes)
    mean_deviation = sum(abs(value - average) for value in start_minutes) / len(start_minutes)
    evidence_score = min(1.0, len(start_minutes) / (minimum_evidence + 2))
    consistency_score = max(0.0, 1.0 - mean_deviation / 120)
    return round(0.4 + 0.6 * evidence_score * consistency_score, 2)


def _count_confidence(evidence_count: int, minimum_evidence: int) -> float:
    return round(0.4 + 0.6 * min(1.0, evidence_count / (minimum_evidence + 2)), 2)
