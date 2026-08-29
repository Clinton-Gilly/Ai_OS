"""An offline, rule-based provider.

This is a development and test fallback, not the intelligence layer: it maps a
handful of common phrasings onto registered tools so AI OS is usable — and the
whole approval, dry-run, and undo path is exercisable — with no API key and no
network. It only ever emits calls for tools that are actually registered, and
says so plainly when it does not understand a request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import LLMResponse, Message, Provider, ToolCall

Rule = tuple[re.Pattern[str], str, Callable[[re.Match[str]], dict[str, Any]]]

# Folder names people refer to by shorthand.
FOLDER_WORDS = {
    "desktop": "Desktop",
    "downloads": "Downloads",
    "documents": "Documents",
    "pictures": "Pictures",
    "music": "Music",
    "videos": "Videos",
    "home": "",
}


def resolve_folder(value: str) -> str:
    """Turn "my desktop" or "downloads/old.txt" into a concrete path."""
    text = value.strip().strip("\"'").replace("\\", "/")
    for word in (" folder", " directory"):
        if text.lower().endswith(word):
            text = text[: -len(word)].strip()
    key = text.lower().removeprefix("my ").strip()
    if key in FOLDER_WORDS:
        suffix = FOLDER_WORDS[key]
        return str(Path.home() / suffix) if suffix else str(Path.home())
    head, _, tail = text.partition("/")
    head_key = head.lower().removeprefix("my ").strip()
    if head_key in FOLDER_WORDS and tail:
        # "downloads/old.txt" means the user's Downloads folder.
        return str(Path.home() / FOLDER_WORDS[head_key] / tail)
    return str(Path(text).expanduser())


RULES: list[Rule] = [
    (re.compile(r"\bwhat time is it\b|\bwhat(?:'s| is) the (?:time|date)\b"
                r"|\bwhat day is it\b|^\s*time\s*$", re.I),
     "system_time", lambda m: {}),
    (re.compile(r"\b(?:disk (?:space|usage)|how much (?:space|storage))\b", re.I),
     "system_disk_usage", lambda m: {}),
    (re.compile(r"\b(?:system|machine|computer) info(?:rmation)?\b", re.I),
     "system_info", lambda m: {}),
    (re.compile(r"\borganiz[es]?\s+(?:my\s+)?(?P<target>[\w .:\\/-]+?)"
                r"(?:\s+by\s+(?P<by>type|date))?\s*$", re.I),
     "fs_organize",
     lambda m: {"path": resolve_folder(m.group("target")),
                "by": (m.group("by") or "type").lower()}),
    (re.compile(r"\b(?:list|show|what(?:'s| is) in)\b[^\w]*(?:my\s+)?(?P<target>[\w .:\\/-]+?)"
                r"\s*(?:folder|directory)?\s*$", re.I),
     "fs_list", lambda m: {"path": resolve_folder(m.group("target"))}),
    (re.compile(r"\b(?:delete|remove|trash)\s+(?P<target>.+?)\s*$", re.I),
     "fs_delete", lambda m: {"path": resolve_folder(m.group("target"))}),
    (re.compile(r"\brename\s+(?P<target>.+?)\s+to\s+(?P<name>[^\s]+)\s*$", re.I),
     "fs_rename",
     lambda m: {"path": resolve_folder(m.group("target")),
                "new_name": m.group("name").strip("\"'")}),
    (re.compile(r"\bmove\s+(?P<target>.+?)\s+(?:to|into)\s+(?P<destination>.+?)\s*$", re.I),
     "fs_move",
     lambda m: {"source": resolve_folder(m.group("target")),
                "destination": resolve_folder(m.group("destination"))}),
    (re.compile(r"\b(?:go to|open|browse to|navigate to)\s+(?P<url>https?://\S+|[\w-]+\.\w{2,}\S*)",
                re.I),
     "browser_open", lambda m: {"target": m.group("url")}),
    (re.compile(r"\bopen\s+(?P<app>chrome|edge|firefox)\s+(?:and\s+)?"
                r"(?:go to|open|navigate to)\s+(?P<url>\S+)", re.I),
     "browser_open",
     lambda m: {"target": m.group("url"), "browser": m.group("app").lower()}),
    (re.compile(r"\b(?:open|launch|start)\s+(?P<app>[\w .-]+?)\s*$", re.I),
     "app_open", lambda m: {"name": m.group("app").strip()}),
    (re.compile(r"\b(?:close|quit|kill)\s+(?P<app>[\w .-]+?)\s*$", re.I),
     "app_close", lambda m: {"name": m.group("app").strip()}),
    (re.compile(r"\b(?:what(?:'s| is) running|running (?:apps|processes))\b", re.I),
     "app_list_running", lambda m: {}),
    (re.compile(r"\b(?:shut ?down|power off|turn off)\b", re.I),
     "power_shutdown", lambda m: {}),
    (re.compile(r"\b(?:restart|reboot)\b", re.I), "power_restart", lambda m: {}),
    (re.compile(r"\b(?:sleep|suspend)\b", re.I), "power_sleep", lambda m: {}),
    (re.compile(r"\block(?:\s+(?:my\s+)?(?:pc|laptop|computer|screen))?\b", re.I),
     "power_lock", lambda m: {}),
    (re.compile(r"\bcancel\s+(?:the\s+)?(?:shutdown|restart)\b", re.I),
     "power_cancel", lambda m: {}),
]


class LocalRuleProvider(Provider):
    """Deterministic intent matching — no network, no API key."""

    name = "local"
    requires_api_key = False

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        # Tool results already came back: summarize instead of acting again.
        if messages and messages[-1].role == "tool":
            return LLMResponse(text=_summarize_results(messages),
                               model=self.model or "rules-v1")

        request = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        ).strip()
        available = {tool["name"] for tool in tools}

        # More specific rules are listed first; the first match wins.
        for pattern, tool_name, build in RULES:
            if tool_name not in available:
                continue
            match = pattern.search(request)
            if match:
                arguments = {k: v for k, v in build(match).items() if v not in (None, "")}
                return LLMResponse(
                    tool_calls=[ToolCall(id=f"local-{tool_name}", name=tool_name,
                                         arguments=arguments)],
                    model=self.model or "rules-v1",
                )

        return LLMResponse(
            text=(
                "I could not match that to a skill using the offline rule provider. "
                "Configure a model provider for full natural-language understanding: "
                "ai-os settings set-provider anthropic"
            ),
            model=self.model or "rules-v1",
        )


def _summarize_results(messages: list[Message]) -> str:
    """Read back the messages from the tool results at the end of the exchange."""
    lines: list[str] = []
    for message in reversed(messages):
        if message.role != "tool":
            break
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            lines.append(message.content)
            continue
        lines.append(str(payload.get("message") or payload.get("status", "")))
    return " ".join(line for line in reversed(lines) if line)
