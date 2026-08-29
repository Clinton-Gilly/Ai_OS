"""Any OpenAI-compatible chat-completions endpoint.

Covers OpenAI itself and, by changing base_url, the providers queued up for
Phase 3 (Groq, DeepSeek, OpenRouter) that speak the same wire format.
"""

from __future__ import annotations

import json
from typing import Any

from .base import LLMError, LLMResponse, Message, Provider, ToolCall, post_json

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleProvider(Provider):
    name = "openai"

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        if not self.api_key:
            raise LLMError(
                f"No API key configured for {self.name}. "
                f"Run: ai-os settings set-key {self.name}"
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._convert(messages),
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                }
                for tool in tools
            ]
        base = (self.base_url or DEFAULT_BASE_URL).rstrip("/")
        data = post_json(f"{base}/chat/completions", payload,
                         {"authorization": f"Bearer {self.api_key}"})
        return self._parse(data)

    def _convert(self, messages: list[Message]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                converted.append({
                    "role": "tool",
                    "tool_call_id": message.tool_call_id or "",
                    "content": message.content,
                })
                continue
            entry: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            converted.append(entry)
        return converted

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("The provider returned no choices.")
        message = choices[0].get("message") or {}
        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) \
                    else raw_arguments
            except json.JSONDecodeError as exc:
                raise LLMError(f"Model sent invalid tool arguments: {exc}") from exc
            tool_calls.append(ToolCall(
                id=call.get("id", ""),
                name=function.get("name", ""),
                arguments=arguments or {},
            ))
        return LLMResponse(
            text=(message.get("content") or "").strip(),
            tool_calls=tool_calls,
            model=data.get("model", self.model),
        )
