from tools.todo_tool import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    get_todos,
)


def test_get_todos_returns_mock_items():
    todos = get_todos()

    assert isinstance(todos, list)
    assert len(todos) == 3
    assert todos[0]["title"] == "学习 OpenAI Agents SDK"
    assert todos[0]["priority"] == "高"
    assert todos[0]["estimated_minutes"] == 90
    assert todos[0]["status"] == "待处理"


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
    todos[0]["title"] = "被修改的任务"

    fresh_todos = get_todos()

    assert fresh_todos[0]["title"] == "学习 OpenAI Agents SDK"


def test_todo_tool_metadata_is_present():
    assert TOOL_NAME == "get_todos"
    assert "待办事项" in TOOL_DESCRIPTION