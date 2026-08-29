"""Claude via the Anthropic Messages API."""

from __future__ import annotations

import json
from typing import Any

from .base import LLMError, LLMResponse, Message, Provider, ToolCall, post_json

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name = "anthropic"

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        if not self.api_key:
            raise LLMError("No Anthropic API key configured. Run: ai-os settings set-key anthropic")

        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2048,
            "messages": self._convert(messages),
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if tools:
            payload["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["parameters"],
                }
                for tool in tools
            ]

        base = (self.base_url or "").rstrip("/")
        url = f"{base}/v1/messages" if base else API_URL
        data = post_json(url, payload, {
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
        })
        return self._parse(data)

    def _convert(self, messages: list[Message]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id or "",
                        "content": message.content,
                    }],
                })
                continue
            if message.role == "assistant" and message.tool_calls:
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    })
                converted.append({"role": "assistant", "content": blocks})
                continue
            converted.append({"role": message.role, "content": message.content})
        return _merge_adjacent(converted)

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content", []) or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                arguments = block.get("input") or {}
                if isinstance(arguments, str):
                    arguments = json.loads(arguments or "{}")
                tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=arguments,
                ))
        return LLMResponse(
            text="".join(text_parts).strip(),
            tool_calls=tool_calls,
            model=data.get("model", self.model),
        )


def _merge_adjacent(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Messages API rejects two consecutive turns with the same role."""
    merged: list[dict[str, Any]] = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"]:
            previous, current = merged[-1]["content"], message["content"]
            if isinstance(previous, str) and isinstance(current, str):
                merged[-1]["content"] = f"{previous}\n{current}"
                continue
            merged[-1]["content"] = _as_blocks(previous) + _as_blocks(current)
            continue
        merged.append(dict(message))
    return merged


def _as_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content)
