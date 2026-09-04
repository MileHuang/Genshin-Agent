"""Todo tool with mock and Todoist read-only providers."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import date
from typing import Any, Literal, Protocol, TypedDict

import httpx

from config.settings import TODOIST_API_BASE_URL, TODOIST_API_TOKEN, TODOIST_TIMEOUT_SECONDS


TOOL_NAME = "get_todos"
TOOL_DESCRIPTION = (
    "Return todo items with priority, estimated duration, deadline, category, "
    "and status."
)

TodoPriority = Literal["low", "medium", "high"]
TodoStatus = Literal["pending", "in_progress", "completed", "skipped"]


class TodoItem(TypedDict):
    id: str
    title: str
    priority: TodoPriority
    estimated_minutes: int
    deadline: str | None
    category: str
    status: TodoStatus


class TodoProvider(Protocol):
    def get_todos(self) -> list[TodoItem]: ...


class TodoistConfigurationError(RuntimeError):
    """Raised when the local Todoist token has not been configured."""


class TodoistAPIError(RuntimeError):
    """Raised when Todoist rejects or cannot complete a request."""


DEFAULT_MOCK_TODOS: tuple[TodoItem, ...] = (
    {
        "id": "todo-001",
        "title": "Study the OpenAI Agents SDK",
        "priority": "high",
        "estimated_minutes": 90,
        "deadline": "18:00",
        "category": "study",
        "status": "pending",
    },
    {
        "id": "todo-002",
        "title": "Outdoor run",
        "priority": "medium",
        "estimated_minutes": 45,
        "deadline": None,
        "category": "health",
        "status": "pending",
    },
    {
        "id": "todo-003",
        "title": "Buy groceries",
        "priority": "medium",
        "estimated_minutes": 40,
        "deadline": "19:30",
        "category": "errand",
        "status": "pending",
    },
)


class MockTodoProvider:
    """In-memory provider used for offline development and tests."""

    def __init__(self, todos: Sequence[TodoItem] = DEFAULT_MOCK_TODOS) -> None:
        self._todos = deepcopy(list(todos))

    def get_todos(self) -> list[TodoItem]:
        return deepcopy(self._todos)


class TodoistProvider:
    """Read active Todoist tasks. This provider never creates or edits tasks."""

    def __init__(
        self,
        *,
        token: str | None = TODOIST_API_TOKEN,
        base_url: str = TODOIST_API_BASE_URL,
        timeout_seconds: float = TODOIST_TIMEOUT_SECONDS,
        max_planning_tasks: int = 10,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.token = token.strip() if isinstance(token, str) else ""
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_planning_tasks = max_planning_tasks
        self._http_client = http_client

    def get_todos(self) -> list[TodoItem]:
        if not self.token:
            raise TodoistConfigurationError(
                "TODOIST_API_TOKEN is missing. Add it to your local .env file."
            )

        client = self._http_client or httpx.Client(timeout=self.timeout_seconds)
        try:
            return _select_planning_tasks(
                self._fetch_all_tasks(client),
                limit=self.max_planning_tasks,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise TodoistConfigurationError(
                    "Todoist rejected TODOIST_API_TOKEN. Generate a new token and update .env."
                ) from exc
            raise TodoistAPIError(
                f"Todoist returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            raise TodoistAPIError(f"Todoist request failed: {exc}") from exc
        finally:
            if self._http_client is None:
                client.close()

    def _fetch_all_tasks(self, client: httpx.Client) -> list[TodoItem]:
        cursor: str | None = None
        todos: list[TodoItem] = []
        while True:
            params: dict[str, str | int] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            response = client.get(
                f"{self.base_url}/tasks",
                headers={"Authorization": f"Bearer {self.token}"},
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise TodoistAPIError("Todoist returned an invalid task list.")
            todos.extend(_to_todo_item(task) for task in payload["results"])
            next_cursor = payload.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                return todos
            cursor = next_cursor


def get_todos(*, provider: TodoProvider | None = None) -> list[TodoItem]:
    """Return todos from an injected provider or the configured default."""

    return (provider or MockTodoProvider()).get_todos()


def _to_todo_item(task: object) -> TodoItem:
    if not isinstance(task, dict):
        raise TodoistAPIError("Todoist returned a malformed task.")
    title = task.get("content")
    task_id = task.get("id")
    if not isinstance(title, str) or not title.strip() or task_id is None:
        raise TodoistAPIError("Todoist task is missing id or content.")
    labels = task.get("labels")
    category = labels[0] if isinstance(labels, list) and labels and isinstance(labels[0], str) else "Todoist"
    return {
        "id": str(task_id),
        "title": title.strip(),
        "priority": _to_priority(task.get("priority")),
        "estimated_minutes": _duration_minutes(task.get("duration")),
        "deadline": _deadline(task.get("deadline") or task.get("due")),
        "category": category,
        "status": "pending",
    }


def _to_priority(value: object) -> TodoPriority:
    if value in (4, 3):
        return "high"
    if value == 2:
        return "medium"
    return "low"


def _duration_minutes(value: object) -> int:
    if not isinstance(value, dict) or value.get("unit") != "minute":
        return 0
    amount = value.get("amount")
    return amount if isinstance(amount, int) and amount > 0 else 0


def _deadline(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    datetime_value = value.get("datetime")
    if isinstance(datetime_value, str) and datetime_value:
        return datetime_value
    date_value = value.get("date")
    return date_value if isinstance(date_value, str) and date_value else None


def _select_planning_tasks(todos: list[TodoItem], *, limit: int) -> list[TodoItem]:
    """Keep the most actionable tasks so one plan is not overloaded."""

    today = date.today().isoformat()

    def rank(todo: TodoItem) -> tuple[int, int, str]:
        deadline = todo["deadline"] or "9999-12-31"
        urgent = 0 if deadline[:10] <= today or todo["priority"] == "high" else 1
        priority = {"high": 0, "medium": 1, "low": 2}[todo["priority"]]
        return urgent, priority, deadline

    return sorted(todos, key=rank)[:limit]
