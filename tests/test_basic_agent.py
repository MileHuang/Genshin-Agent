import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from agents.basic_agent import BasicAgent, KimiAPIError


def test_run_sends_kimi_k3_request_and_records_complete_history():
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "private reasoning",
                            "content": "A practical plan",
                        }
                    }
                ]
            },
        )

    async def scenario() -> tuple[str, list[dict]]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        agent = BasicAgent(
            api_key="test-key",
            reasoning_effort="max",
            http_client=client,
        )
        try:
            result = await agent.run("Plan my day")
            return result, agent.messages
        finally:
            await client.aclose()

    result, history = asyncio.run(scenario())

    assert result == "A practical plan"
    assert requests[0]["model"] == "kimi-k3"
    assert requests[0]["reasoning_effort"] == "max"
    assert requests[0]["stream"] is False
    assert [message["role"] for message in requests[0]["messages"]] == [
        "system",
        "user",
    ]
    assert history[-1]["reasoning_content"] == "private reasoning"
    assert history[-1]["content"] == "A practical plan"


def test_run_structured_uses_sdk_schema_and_returns_pydantic_model():
    requests: list[dict] = []

    class Answer(BaseModel):
        title: str
        count: int

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"title":"Study","count":2}',
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async def scenario() -> tuple[Answer, list[dict]]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        agent = BasicAgent(
            api_key="test-key",
            reasoning_effort="max",
            http_client=client,
        )
        try:
            result = await agent.run_structured("Make a plan", Answer)
            return result, agent.messages
        finally:
            await client.aclose()

    result, history = asyncio.run(scenario())

    assert result == Answer(title="Study", count=2)
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert requests[0]["reasoning_effort"] == "max"
    assert history[-1]["content"] == '{"title":"Study","count":2}'


def test_stream_yields_answer_content_and_preserves_reasoning():
    stream_body = """data: {"choices":[{"delta":{"reasoning_content":"think "}}]}

data: {"choices":[{"delta":{"reasoning_content":"carefully"}}]}

data: {"choices":[{"delta":{"content":"Hello "}}]}

data: {"choices":[]}

data: {"choices":[{"delta":{"content":"world"}}]}

data: {"choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]

"""

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=stream_body,
            headers={"Content-Type": "text/event-stream"},
        )

    async def scenario() -> tuple[list[str], list[dict]]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        agent = BasicAgent(api_key="test-key", http_client=client)
        chunks: list[str] = []
        try:
            async for chunk in agent.stream("Say hello"):
                chunks.append(chunk)
            return chunks, agent.messages
        finally:
            await client.aclose()

    chunks, history = asyncio.run(scenario())

    assert chunks == ["Hello ", "world"]
    assert history[-1]["content"] == "Hello world"
    assert history[-1]["reasoning_content"] == "think carefully"


def test_stream_failure_does_not_commit_partial_history():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
            headers={"Content-Type": "text/event-stream"},
        )

    async def scenario() -> list[dict]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        agent = BasicAgent(api_key="test-key", http_client=client)
        try:
            with pytest.raises(KimiAPIError, match="before a finish event"):
                async for _ in agent.stream("Incomplete stream"):
                    pass
            return agent.messages
        finally:
            await client.aclose()

    history = asyncio.run(scenario())
    assert [message["role"] for message in history] == ["system"]


def test_system_prompt_and_messages_can_be_managed():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None))
    agent = BasicAgent(
        api_key="test-key",
        system_prompt="Initial prompt",
        http_client=client,
    )

    agent.add_message("user", "Remember this")
    copied_messages = agent.messages
    copied_messages[-1]["content"] = "mutated copy"
    assert agent.messages[-1]["content"] == "Remember this"

    agent.set_system_prompt("Improved prompt")
    assert agent.system_prompt == "Improved prompt"
    assert agent.messages == [{"role": "system", "content": "Improved prompt"}]
    asyncio.run(client.aclose())


def test_missing_key_and_invalid_inputs_raise_clear_errors(monkeypatch):
    monkeypatch.setattr("agents.basic_agent.MOONSHOT_API_KEY", None)

    with pytest.raises(ValueError, match="Missing MOONSHOT_API_KEY"):
        BasicAgent(api_key=None)
    with pytest.raises(ValueError, match="reasoning_effort"):
        BasicAgent(api_key="test-key", reasoning_effort="medium")


def test_blank_prompt_is_rejected_before_network_call():
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network should not be called")

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        agent = BasicAgent(api_key="test-key", http_client=client)
        try:
            with pytest.raises(ValueError, match="prompt must not be blank"):
                await agent.run("   ")
        finally:
            await client.aclose()

    asyncio.run(scenario())
