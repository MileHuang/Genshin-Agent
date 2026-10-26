"""Aggregate repeated edit events into behavioral preferences."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from tools.behavior_preference import BehaviorPreference
from tools.edit_event import EditAction, EditEvent


class PreferenceAggregator:
    """Project bounded, scope-aware evidence into current preferences.

    Temporary edits do not train habits. Persistent declarations start a new
    evidence epoch and suffice alone. Unknown edits need repeated, compatible
    evidence. Confidence is a heuristic support score, not a probability.
    Recency uses observation order (two observations per half-life), not age.
    """

    def __init__(
        self,
        minimum_evidence: int = 3,
        *,
        window_size: int = 12,
        time_tolerance_minutes: int = 60,
    ) -> None:
        if type(minimum_evidence) is not int or minimum_evidence < 1:
            raise ValueError("minimum_evidence must be a positive integer")
        if type(window_size) is not int or window_size < minimum_evidence:
            raise ValueError("window_size must be at least minimum_evidence")
        if type(time_tolerance_minutes) is not int or time_tolerance_minutes < 0:
            raise ValueError("time_tolerance_minutes must be non-negative")
        self.minimum_evidence = minimum_evidence
        self.window_size = window_size
        self.time_tolerance_minutes = time_tolerance_minutes

    def aggregate(self, events: Iterable[EditEvent]) -> list[BehaviorPreference]:
        """Produce preferred-time, avoid-time, and priority preferences."""

        grouped: dict[tuple[str, str], list[EditEvent]] = defaultdict(list)
        seen: set[tuple[str, str]] = set()
        for event in events:
            if not isinstance(event, EditEvent):
                raise TypeError("every event must be an EditEvent")
            identity = (event.user_id, event.event_id)
            if identity in seen:
                continue
            seen.add(identity)
            if event.scope != "temporary":
                grouped[event.user_id, event.activity_type].append(event)

        preferences: list[BehaviorPreference] = []
        for (user_id, activity_type), history in sorted(grouped.items()):
            history.sort(key=lambda event: event.timestamp)
            declarations = [i for i, event in enumerate(history) if event.scope == "persistent"]
            if declarations:
                history = history[declarations[-1]:]
            history = history[-self.window_size:]
            weights = {
                event.event_id: 0.5 ** ((len(history) - 1 - i) / 2)
                for i, event in enumerate(history)
            }
            total_weight = sum(weights.values())
            for attribute, actions in (
                ("preferred_time_range", {EditAction.MOVE, EditAction.ACCEPT}),
                ("avoid_time_range", {EditAction.SKIP}),
                ("deprioritize_activity", {EditAction.DELETE}),
            ):
                matching = [event for event in history if event.action in actions]
                clusters = (
                    [matching] if attribute == "deprioritize_activity"
                    else self._clusters(matching)
                )
                eligible = [
                    cluster for cluster in clusters if cluster and (
                        len(cluster) >= self.minimum_evidence
                        or any(event.scope == "persistent" for event in cluster)
                    )
                ]
                if not eligible:
                    continue
                support = max(eligible, key=lambda cluster: sum(weights[e.event_id] for e in cluster))
                confidence = sum(weights[e.event_id] for e in support) / total_weight
                if attribute == "deprioritize_activity":
                    preference = BehaviorPreference(
                        category="planning", activity_type=activity_type,
                        attribute=attribute, value={"level": "low"},
                        confidence=round(confidence, 4), evidence_count=len(support),
                        evidence_event_ids=tuple(event.event_id for event in support),
                        user_id=user_id, created_at=support[0].timestamp,
                        updated_at=support[-1].timestamp,
                    )
                else:
                    preference = self._time_range_preference(
                        user_id, activity_type, support, attribute=attribute,
                        weights=weights, confidence=confidence,
                    )
                preferences.append(preference)
        return preferences

    def _clusters(self, events: list[EditEvent]) -> list[list[EditEvent]]:
        clusters: list[list[EditEvent]] = []
        for event in events:
            # Bound the complete cluster, so successive small shifts cannot
            # bridge morning and evening into one unsupported midpoint.
            compatible = [cluster for cluster in clusters if all(
                max(abs(_feedback_start_minutes(event) - _feedback_start_minutes(other)),
                    abs(_feedback_end_minutes(event) - _feedback_end_minutes(other)))
                <= self.time_tolerance_minutes for other in cluster
            )]
            if compatible:
                compatible[-1].append(event)
            else:
                clusters.append([event])
        return clusters

    def _time_range_preference(
        self,
        user_id: str,
        activity_type: str,
        activity_events: list[EditEvent],
        *,
        attribute: str,
        weights: dict[str, float],
        confidence: float,
    ) -> BehaviorPreference:
        start_minutes = [_feedback_start_minutes(event) for event in activity_events]
        end_minutes = [_feedback_end_minutes(event) for event in activity_events]
        mass = sum(weights[event.event_id] for event in activity_events)
        preferred_start = round(sum(
            value * weights[event.event_id]
            for value, event in zip(start_minutes, activity_events)
        ) / mass)
        preferred_end = round(sum(
            value * weights[event.event_id]
            for value, event in zip(end_minutes, activity_events)
        ) / mass)

        return BehaviorPreference(
            category="scheduling",
            activity_type=activity_type,
            attribute=attribute,
            value={
                "start": _format_minutes(preferred_start),
                "end": _format_minutes(preferred_end),
            },
            confidence=round(confidence, 4),
            evidence_count=len(activity_events),
            evidence_event_ids=tuple(event.event_id for event in activity_events),
            user_id=user_id,
            created_at=activity_events[0].timestamp,
            updated_at=activity_events[-1].timestamp,
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
