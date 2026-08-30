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

from ..planner import PLAN_CONTEXT_MARKER, PLAN_MARKER, parse_context_steps
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


def _reminder_name(message: str) -> str:
    """A short, stable task name taken from the reminder text."""
    words = re.findall(r"[A-Za-z0-9]+", message)[:4]
    return " ".join(words) or "reminder"


RULES: list[Rule] = [
    # Diagnostics and memory phrasings are specific, so they are matched first.
    (re.compile(r"\bwhy(?:'s| is)?\b[^.]*\b(?:slow|sluggish|laggy|freezing)\b"
                r"|\bdiagnos(?:e|tic|tics)\b"
                r"|\bwhat(?:'s| is) (?:making|slowing) (?:it|my|the)\b", re.I),
     "diag_report", lambda m: {}),
    (re.compile(r"\b(?:top|heaviest|biggest|hungriest) (?:processes|apps|programs)\b"
                r"|\bwhat(?:'s| is) using (?:up )?(?:my |the )?(?:memory|ram)\b", re.I),
     "diag_top_processes", lambda m: {}),
    (re.compile(r"\bstartup (?:programs|items|apps|applications)\b"
                r"|\bwhat starts (?:up )?(?:when i (?:sign|log) in|on boot)\b", re.I),
     "diag_startup_items", lambda m: {}),
    (re.compile(r"\b(?:large|big|biggest|largest) files\b(?:\s+(?:in|under|on)\s+"
                r"(?:my\s+)?(?P<target>[\w .:\\/-]+?))?\s*$", re.I),
     "diag_large_files",
     lambda m: {"path": resolve_folder(m.group("target") or "home")}),
    (re.compile(r"\bwhat do you remember\b|\bshow (?:me )?(?:your |my )?memor(?:y|ies)\b"
                r"|\bwhat(?:'s| is) in (?:your )?memory\b", re.I),
     "memory_list", lambda m: {}),
    (re.compile(r"\bforget everything\b|\bclear (?:your |my )?memory\b"
                r"|\bwipe (?:your |my )?memory\b", re.I),
     "memory_forget", lambda m: {}),
    (re.compile(r"\bremember that (?P<key>.+?) (?:is|are|=) (?P<value>.+?)\s*$", re.I),
     "memory_remember",
     lambda m: {"kind": "fact", "key": m.group("key").strip(),
                "value": m.group("value").strip()}),
    (re.compile(r"\bremind me\s+(?P<when>(?:at|in)\s+[\w: ]+?)\s+to\s+"
                r"(?P<message>.+?)\s*$", re.I),
     "schedule_reminder",
     lambda m: {"name": _reminder_name(m.group("message")),
                "message": m.group("message").strip(),
                "when": m.group("when").strip()}),
    (re.compile(r"\bremind me\s+(?:to\s+)?(?P<message>.+?)\s+"
                r"(?P<when>(?:at|in)\s+[\w: ]+?)\s*$", re.I),
     "schedule_reminder",
     lambda m: {"name": _reminder_name(m.group("message")),
                "message": m.group("message").strip(),
                "when": m.group("when").strip()}),
    (re.compile(r"\b(?:list|show)\b[^.]*\bscheduled tasks\b"
                r"|\bwhat(?:'s| is) scheduled\b", re.I),
     "schedule_list", lambda m: {}),
    (re.compile(r"\bwhat time is it\b|\bwhat(?:'s| is) the (?:time|date)\b"
                r"|\b(?:tell|give) me the (?:time|date)\b"
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
        # A planning request: split the sentence rather than act on it.
        if any(PLAN_MARKER in message.content for message in messages
               if message.role == "system"):
            request = next(
                (m.content for m in reversed(messages) if m.role == "user"), "")
            return LLMResponse(text=json.dumps(_plan_offline(request)),
                               model=self.model or "rules-v1")

        available = {tool["name"] for tool in tools}

        # Executing an approved plan: walk it a step at a time, using the count
        # of results already returned as the position in the plan.
        plan_context = next(
            (m.content for m in messages
             if m.role == "system" and PLAN_CONTEXT_MARKER in m.content), "")
        if plan_context:
            steps = parse_context_steps(plan_context)
            done = sum(1 for message in messages if message.role == "tool")
            if done < len(steps):
                call = self._match(steps[done].description, available)
                if call is not None:
                    return LLMResponse(tool_calls=[call],
                                       model=self.model or "rules-v1")
            if done:
                return LLMResponse(text=_summarize_results(messages),
                                   model=self.model or "rules-v1")

        # Tool results already came back: summarize instead of acting again.
        if messages and messages[-1].role == "tool":
            return LLMResponse(text=_summarize_results(messages),
                               model=self.model or "rules-v1")

        request = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        ).strip()

        call = self._match(request, available)
        if call is not None:
            return LLMResponse(tool_calls=[call], model=self.model or "rules-v1")

        return LLMResponse(
            text=(
                "I could not match that to a skill using the offline rule provider. "
                "Configure a model provider for full natural-language understanding: "
                "ai-os settings set-provider anthropic"
            ),
            model=self.model or "rules-v1",
        )

    def _match(self, text: str, available: set[str]) -> ToolCall | None:
        """First matching rule wins; more specific rules are listed first."""
        for pattern, tool_name, build in RULES:
            if tool_name not in available:
                continue
            match = pattern.search(text)
            if match:
                arguments = {k: v for k, v in build(match).items()
                             if v not in (None, "")}
                return ToolCall(id=f"local-{tool_name}", name=tool_name,
                                arguments=arguments)
        return None


def _summarize_results(messages: list[Message]) -> str:
    """Read back every tool result in the exchange, in the order they happened."""
    lines: list[str] = []
    for message in messages:
        if message.role != "tool":
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            lines.append(message.content)
            continue
        lines.append(str(payload.get("message") or payload.get("status", "")))
    return " ".join(line for line in lines if line)


# Conjunctions that separate one instruction from the next, longest first so
# "and then" is not split by the shorter "and".
_SPLITTERS = (
    " and then ", ", then ", " then ", " after that ", " followed by ",
    " and also ", " as well as ", ", and ", " and ",
)


def split_request(request: str) -> list[str]:
    """Break a compound sentence into the instructions it contains."""
    parts = [request.strip()]
    for splitter in _SPLITTERS:
        expanded: list[str] = []
        for part in parts:
            expanded.extend(piece.strip() for piece in part.lower().split(splitter))
        parts = [piece for piece in expanded if piece]
    return parts


def _plan_offline(request: str) -> dict[str, Any]:
    """Build a plan by matching each clause against the rule table."""
    steps: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for clause in split_request(request):
        matched = None
        for pattern, tool_name, _build in RULES:
            if pattern.search(clause):
                matched = tool_name
                break
        if matched:
            steps.append({"description": clause, "action": matched})
        else:
            unmatched.append(clause)
    notes = ""
    if unmatched:
        notes = ("The offline rule provider could not match: "
                 + "; ".join(unmatched)
                 + ". Configure a model provider for full understanding.")
    return {"steps": steps, "notes": notes}
