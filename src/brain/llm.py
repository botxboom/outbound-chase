"""Unified LLM client — OpenAI-compatible endpoints or native Anthropic."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI

from src.config import settings


@dataclass
class ToolCallFunction:
    name: str
    arguments: str


@dataclass
class ToolCall:
    function: ToolCallFunction


@dataclass
class ChatMessage:
    content: str | None
    tool_calls: list[ToolCall]


def _ollama_extra() -> dict:
    if not settings.llm_disable_think:
        return {}
    if "qwen3" in settings.llm_model.lower():
        return {"think": False}
    return {}


def _openai_tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function", {})
        converted.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return converted


def _split_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    chat_messages: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            if content:
                system_parts.append(str(content))
        elif role in ("user", "assistant"):
            chat_messages.append({"role": role, "content": str(content)})
    return "\n\n".join(system_parts), chat_messages


async def _openai_compatible_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int | None,
    temperature: float | None,
) -> ChatMessage:
    client = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
    )
    extra = _ollama_extra()
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "max_tokens": max_tokens or settings.llm_max_tokens,
        "temperature": temperature if temperature is not None else settings.llm_temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if extra:
        kwargs["extra_body"] = extra

    response = await client.chat.completions.create(**kwargs)
    message = response.choices[0].message
    tool_calls = [
        ToolCall(
            function=ToolCallFunction(
                name=tc.function.name,
                arguments=tc.function.arguments or "{}",
            )
        )
        for tc in (message.tool_calls or [])
    ]
    return ChatMessage(content=message.content, tool_calls=tool_calls)


async def _anthropic_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int | None,
    temperature: float | None,
) -> ChatMessage:
    from anthropic import AsyncAnthropic

    system, chat_messages = _split_messages(messages)
    if not chat_messages:
        chat_messages = [{"role": "user", "content": "Hello"}]

    client = AsyncAnthropic(
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": max_tokens or settings.llm_max_tokens,
        "messages": chat_messages,
        "temperature": temperature if temperature is not None else settings.llm_temperature,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = _openai_tools_to_anthropic(tools)

    response = await client.messages.create(**kwargs)

    content_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in response.content:
        if block.type == "text":
            content_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(
                ToolCall(
                    function=ToolCallFunction(
                        name=block.name,
                        arguments=json.dumps(block.input),
                    )
                )
            )

    return ChatMessage(content="".join(content_parts) or None, tool_calls=tool_calls)


async def chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> ChatMessage:
    """Run a chat completion using the configured LLM provider."""
    if settings.llm_provider.lower() == "anthropic":
        return await _anthropic_completion(messages, tools, max_tokens, temperature)
    return await _openai_compatible_completion(messages, tools, max_tokens, temperature)
