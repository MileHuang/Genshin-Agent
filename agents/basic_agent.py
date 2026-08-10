"""Async Kimi K3 client built on the OpenAI-compatible Python SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any, TypeVar

import httpx
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel

from config.settings import (
    AGENT_SYSTEM_PROMPT,
    KIMI_BASE_URL,
    KIMI_MODEL,
    KIMI_REASONING_EFFORT,
    KIMI_TIMEOUT_SECONDS,
    MOONSHOT_API_KEY,
)


Message = dict[str, Any]
StructuredResponse = TypeVar("StructuredResponse", bound=BaseModel)
VALID_ROLES = {"system", "user", "assistant", "tool"}
VALID_REASONING_EFFORTS = {"low", "high", "max"}


class KimiAPIError(RuntimeError):
    """Raised when Kimi returns an invalid or unsuccessful response."""


class BasicAgent:
    """A small async Kimi K3 agent that owns conversation history.

    Moonshot exposes an OpenAI-compatible Chat Completions API, so transport,
    response models, and streaming are delegated to ``AsyncOpenAI``. A custom
    SDK or HTTP client can still be injected for offline tests.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = KIMI_MODEL,
        base_url: str = KIMI_BASE_URL,
        reasoning_effort: str = KIMI_REASONING_EFFORT,
        system_prompt: str = AGENT_SYSTEM_PROMPT,
        timeout_seconds: float = KIMI_TIMEOUT_SECONDS,
        sdk_client: AsyncOpenAI | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved_api_key = api_key or MOONSHOT_API_KEY
        if not resolved_api_key:
            raise ValueError(
                "Missing MOONSHOT_API_KEY. Add it to .env or the process "
                "environment."
            )
        if not model.strip():
            raise ValueError("model must not be blank")
        if reasoning_effort not in VALID_REASONING_EFFORTS:
            allowed = ", ".join(sorted(VALID_REASONING_EFFORTS))
            raise ValueError(f"reasoning_effort must be one of: {allowed}")
        if not system_prompt.strip():
            raise ValueError("system_prompt must not be blank")

        self.api_key = resolved_api_key
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.reasoning_effort = reasoning_effort
        self._system_prompt = system_prompt.strip()
        if sdk_client is not None and http_client is not None:
            raise ValueError("sdk_client and http_client cannot both be provided")
        self._owns_sdk_client = sdk_client is None and http_client is None
        self._sdk_client = sdk_client or AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout_seconds,
            http_client=http_client,
        )
        self._messages: list[Message] = []
        self.reset_messages()

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def messages(self) -> list[Message]:
        """Return a defensive copy of the current conversation."""

        return deepcopy(self._messages)

    def set_system_prompt(self, prompt: str) -> None:
        """Replace the system prompt and start a fresh conversation."""

        if not prompt.strip():
            raise ValueError("system prompt must not be blank")
        self._system_prompt = prompt.strip()
        self.reset_messages()

    def reset_messages(self) -> None:
        """Clear conversation history while retaining the system prompt."""

        self._messages = [
            {"role": "system", "content": self._system_prompt}
        ]

    def add_message(self, role: str, content: str, **extra: Any) -> None:
        """Append a validated message to the conversation."""

        if role not in VALID_ROLES:
            allowed = ", ".join(sorted(VALID_ROLES))
            raise ValueError(f"role must be one of: {allowed}")
        if not content.strip() and not extra:
            raise ValueError("message content must not be blank")
        self._messages.append(
            {"role": role, "content": content, **deepcopy(extra)}
        )

    async def run(self, prompt: str) -> str:
        """Send a prompt asynchronously and return the final answer text."""

        request_messages = self._messages_for_prompt(prompt)

        try:
            response = await self._sdk_client.chat.completions.create(
                model=self.model,
                messages=request_messages,
                stream=False,
                extra_body=self._kimi_request_options(),
            )
        except OpenAIError as exc:
            raise KimiAPIError(f"Kimi request failed: {exc}") from exc

        try:
            raw_message = response.choices[0].message.model_dump(
                exclude_unset=True
            )
        except (AttributeError, IndexError, TypeError) as exc:
            raise KimiAPIError("Kimi returned an invalid chat response") from exc

        assistant_message = self._normalise_assistant_message(raw_message)
        self._messages = request_messages + [assistant_message]
        return assistant_message.get("content") or ""

    async def run_structured(
        self,
        prompt: str,
        response_model: type[StructuredResponse],
    ) -> StructuredResponse:
        """Return a Pydantic-validated response using SDK structured output."""

        request_messages = self._messages_for_prompt(prompt)
        try:
            response = await self._sdk_client.chat.completions.parse(
                model=self.model,
                messages=request_messages,
                response_format=response_model,
                extra_body=self._kimi_request_options(),
            )
        except OpenAIError as exc:
            raise KimiAPIError(f"Kimi structured request failed: {exc}") from exc

        try:
            raw_message = response.choices[0].message
            parsed = raw_message.parsed
            assistant_message = self._normalise_assistant_message(
                raw_message.model_dump(
                    exclude_unset=True,
                    exclude={"parsed"},
                    warnings=False,
                )
            )
        except (AttributeError, IndexError, TypeError) as exc:
            raise KimiAPIError(
                "Kimi returned an invalid structured response"
            ) from exc

        if parsed is None:
            raise KimiAPIError("Kimi did not return the requested structure")
        self._messages = request_messages + [assistant_message]
        return parsed

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        """Yield final-answer text deltas while retaining full Kimi history.

        Kimi K3 streams ``reasoning_content`` separately from ``content``. Only
        final-answer content is yielded to the caller, while reasoning and tool
        call fields are retained on the assistant message for the next turn.
        """

        request_messages = self._messages_for_prompt(prompt)
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, Message] = {}
        received_finish = False

        try:
            response = await self._sdk_client.chat.completions.create(
                model=self.model,
                messages=request_messages,
                stream=True,
                extra_body=self._kimi_request_options(),
            )
            async with response:
                async for chunk in response:
                    choices = chunk.choices
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.finish_reason is not None:
                        received_finish = True
                    delta = choice.delta.model_dump(exclude_unset=True)

                    reasoning = delta.get("reasoning_content")
                    if isinstance(reasoning, str) and reasoning:
                        reasoning_parts.append(reasoning)

                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        content_parts.append(content)
                        yield content

                    self._merge_tool_call_deltas(
                        tool_calls,
                        delta.get("tool_calls"),
                    )
        except KimiAPIError:
            raise
        except OpenAIError as exc:
            raise KimiAPIError(f"Kimi streaming request failed: {exc}") from exc

        if not received_finish:
            raise KimiAPIError("Kimi stream ended before a finish event")

        assistant_message: Message = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        if reasoning_parts:
            assistant_message["reasoning_content"] = "".join(reasoning_parts)
        if tool_calls:
            assistant_message["tool_calls"] = [
                tool_calls[index] for index in sorted(tool_calls)
            ]

        self._messages = request_messages + [assistant_message]

    async def aclose(self) -> None:
        """Close the internally-created OpenAI SDK client."""

        if self._owns_sdk_client:
            await self._sdk_client.close()

    async def __aenter__(self) -> BasicAgent:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _messages_for_prompt(self, prompt: str) -> list[Message]:
        if not prompt.strip():
            raise ValueError("prompt must not be blank")
        return self.messages + [{"role": "user", "content": prompt.strip()}]

    def _kimi_request_options(self) -> dict[str, str]:
        # ``max`` is a Kimi extension that is not present in every OpenAI SDK
        # type definition. ``extra_body`` still sends it as a top-level field.
        return {"reasoning_effort": self.reasoning_effort}

    @staticmethod
    def _normalise_assistant_message(raw_message: object) -> Message:
        if not isinstance(raw_message, dict):
            raise KimiAPIError("Kimi returned an invalid assistant message")

        content = raw_message.get("content")
        if content is not None and not isinstance(content, str):
            raise KimiAPIError("Kimi returned non-text assistant content")

        message: Message = {
            "role": "assistant",
            "content": content or "",
        }
        for field in ("reasoning_content", "tool_calls"):
            value = raw_message.get(field)
            if value is not None:
                message[field] = deepcopy(value)
        return message

    @staticmethod
    def _merge_tool_call_deltas(
        accumulated: dict[int, Message],
        fragments: object,
    ) -> None:
        if not isinstance(fragments, list):
            return

        for fallback_index, fragment in enumerate(fragments):
            if not isinstance(fragment, dict):
                continue
            index = fragment.get("index", fallback_index)
            if not isinstance(index, int):
                continue

            target = accumulated.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            for field in ("id", "type"):
                value = fragment.get(field)
                if isinstance(value, str) and value:
                    target[field] = (
                        f"{target.get(field, '')}{value}"
                        if field == "id"
                        else value
                    )

            function_fragment = fragment.get("function")
            if not isinstance(function_fragment, dict):
                continue
            target_function = target["function"]
            if not isinstance(target_function, dict):
                target_function = {"name": "", "arguments": ""}
                target["function"] = target_function
            for field in ("name", "arguments"):
                value = function_fragment.get(field)
                if isinstance(value, str) and value:
                    target_function[field] = f"{target_function.get(field, '')}{value}"


_default_agent: BasicAgent | None = None


def _get_default_agent() -> BasicAgent:
    global _default_agent
    if _default_agent is None:
        _default_agent = BasicAgent()
    return _default_agent


async def ask_kimi(prompt: str) -> str:
    """Backward-compatible one-shot helper using the default agent."""

    return await _get_default_agent().run(prompt)


async def ask_kimi_stream(prompt: str) -> AsyncIterator[str]:
    """Backward-compatible streaming helper using the default agent."""

    async for token in _get_default_agent().stream(prompt):
        yield token
