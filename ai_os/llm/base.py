"""Provider-neutral chat types.

Skills, the supervisor, and the CLI only ever see these types. Each provider
translates them to and from its own wire format, so swapping models never
touches core logic.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class LLMError(RuntimeError):
    """A provider could not be reached or refused the request."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    role: str                       # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""


class Provider:
    """Interface every LLM backend implements."""

    name: str = ""
    requires_api_key: bool = True

    def __init__(self, api_key: str = "", model: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def available(self) -> bool:
        return bool(self.api_key) or not self.requires_api_key

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        raise NotImplementedError  # pragma: no cover - interface


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str],
              timeout: int = 60) -> dict[str, Any]:
    """Minimal JSON POST so the core has no third-party HTTP dependency."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("content-type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Could not reach {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"Invalid JSON from {url}: {exc}") from exc
