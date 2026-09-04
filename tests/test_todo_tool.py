import httpx

from tools.todo_tool import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    TodoistProvider,
    get_todos,
)


def test_get_todos_returns_mock_items():
    todos = get_todos()

    assert isinstance(todos, list)
    assert len(todos) == 3
    assert todos[0]["title"] == "Study the OpenAI Agents SDK"
    assert todos[0]["priority"] == "high"
    assert todos[0]["estimated_minutes"] == 90
    assert todos[0]["status"] == "pending"


def test_todo_items_have_required_fields():
    required_fields = {
        "id",
        "title",
        "priority",
        "estimated_minutes",
        "deadline",
        "category",
        "status",
    }

    for todo in get_todos():
        assert required_fields <= todo.keys()


def test_get_todos_returns_defensive_copy():
    todos = get_todos()
    todos[0]["title"] = "Modified task"

    fresh_todos = get_todos()

    assert fresh_todos[0]["title"] == "Study the OpenAI Agents SDK"


def test_todo_tool_metadata_is_present():
    assert TOOL_NAME == "get_todos"
    assert "todo items" in TOOL_DESCRIPTION


def test_todoist_provider_maps_active_tasks_and_follows_pages():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("cursor") == "page-two":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "task-2",
                            "content": "Read article",
                            "priority": 1,
                            "labels": [],
                        }
                    ],
                    "next_cursor": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "task-1",
                        "content": "Finish report",
                        "priority": 4,
                        "labels": ["work"],
                        "duration": {"amount": 45, "unit": "minute"},
                        "due": {"datetime": "2026-08-25T18:00:00+08:00"},
                    }
                ],
                "next_cursor": "page-two",
            },
        )

    provider = TodoistProvider(
        token="test-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    todos = provider.get_todos()

    assert todos == [
        {
            "id": "task-1",
            "title": "Finish report",
            "priority": "high",
            "estimated_minutes": 45,
            "deadline": "2026-08-25T18:00:00+08:00",
            "category": "work",
            "status": "pending",
        },
        {
            "id": "task-2",
            "title": "Read article",
            "priority": "low",
            "estimated_minutes": 0,
            "deadline": None,
            "category": "Todoist",
            "status": "pending",
        },
    ]
    assert len(requests) == 2
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[1].url.params["cursor"] == "page-two"
