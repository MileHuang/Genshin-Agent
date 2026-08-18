"""将用户对计划的反馈转换为可持久化的 EditEvent。"""

from __future__ import annotations

from datetime import datetime

from tools.edit_event import EditAction, EditEvent, EditEventStore


class FeedbackService:
    """计划反馈的应用层入口。

    UI、命令行或 API 只需调用这里的四个动作方法，不需要自己处理
    EditEvent 的构造与保存细节。
    """

    def __init__(self, event_store: EditEventStore) -> None:
        self.event_store = event_store

    def accept(
        self,
        *,
        activity_type: str,
        original_start: datetime,
        original_end: datetime,
        source_plan_id: str,
        user_id: str = "default",
        reason: str | None = None,
    ) -> EditEvent:
        return self._record(
            action=EditAction.ACCEPT,
            activity_type=activity_type,
            original_start=original_start,
            original_end=original_end,
            source_plan_id=source_plan_id,
            user_id=user_id,
            reason=reason,
        )

    def move(
        self,
        *,
        activity_type: str,
        original_start: datetime,
        original_end: datetime,
        new_start: datetime,
        new_end: datetime,
        source_plan_id: str,
        user_id: str = "default",
        reason: str | None = None,
    ) -> EditEvent:
        return self._record(
            action=EditAction.MOVE,
            activity_type=activity_type,
            original_start=original_start,
            original_end=original_end,
            new_start=new_start,
            new_end=new_end,
            source_plan_id=source_plan_id,
            user_id=user_id,
            reason=reason,
        )

    def delete(
        self,
        *,
        activity_type: str,
        original_start: datetime,
        original_end: datetime,
        source_plan_id: str,
        user_id: str = "default",
        reason: str | None = None,
    ) -> EditEvent:
        return self._record(
            action=EditAction.DELETE,
            activity_type=activity_type,
            original_start=original_start,
            original_end=original_end,
            source_plan_id=source_plan_id,
            user_id=user_id,
            reason=reason,
        )

    def skip(
        self,
        *,
        activity_type: str,
        original_start: datetime,
        original_end: datetime,
        source_plan_id: str,
        user_id: str = "default",
        reason: str | None = None,
    ) -> EditEvent:
        return self._record(
            action=EditAction.SKIP,
            activity_type=activity_type,
            original_start=original_start,
            original_end=original_end,
            source_plan_id=source_plan_id,
            user_id=user_id,
            reason=reason,
        )

    def _record(
        self,
        *,
        action: EditAction,
        activity_type: str,
        original_start: datetime,
        original_end: datetime,
        source_plan_id: str,
        user_id: str,
        new_start: datetime | None = None,
        new_end: datetime | None = None,
        reason: str | None = None,
    ) -> EditEvent:
        event = EditEvent(
            action=action,
            activity_type=activity_type,
            original_start=original_start,
            original_end=original_end,
            new_start=new_start,
            new_end=new_end,
            source_plan_id=source_plan_id,
            user_id=user_id,
            reason=reason,
        )
        self.event_store.append(event)
        return event
