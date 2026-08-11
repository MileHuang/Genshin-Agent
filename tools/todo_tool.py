"""每日计划用的模拟待办事项工具。"""

from __future__ import annotations

from copy import deepcopy
from typing import Literal, TypedDict


TOOL_NAME = "get_todos"
TOOL_DESCRIPTION = (
    "返回今天的待办事项，包括优先级、预计耗时、截止时间、分类和状态。"
)


TodoPriority = Literal["低", "中", "高"]
TodoStatus = Literal["待处理", "进行中", "已完成", "已跳过"]


class TodoItem(TypedDict):
    id: str
    title: str
    priority: TodoPriority
    estimated_minutes: int
    deadline: str | None
    category: str
    status: TodoStatus


DEFAULT_MOCK_TODOS: tuple[TodoItem, ...] = (
    {
        "id": "todo-001",
        "title": "学习 OpenAI Agents SDK",
        "priority": "高",
        "estimated_minutes": 90,
        "deadline": "18:00",
        "category": "学习",
        "status": "待处理",
    },
    {
        "id": "todo-002",
        "title": "户外跑步",
        "priority": "中",
        "estimated_minutes": 45,
        "deadline": None,
        "category": "健康",
        "status": "待处理",
    },
    {
        "id": "todo-003",
        "title": "买菜",
        "priority": "中",
        "estimated_minutes": 40,
        "deadline": "19:30",
        "category": "生活",
        "status": "待处理",
    },
)


def get_todos() -> list[TodoItem]:
    """返回每日计划 MVP 使用的模拟待办事项。"""

    return deepcopy(list(DEFAULT_MOCK_TODOS))