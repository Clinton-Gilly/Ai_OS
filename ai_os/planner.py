"""The planner.

A request like "prepare my laptop for development" is not one action. The
planner asks the model to break it into an ordered list of steps first, shows
that list to the user as a whole, and only then lets the normal tool loop run —
so you approve the shape of the work before any of it starts, and each
individual action still goes through the safety engine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .llm import LLMError, Message, Provider

# The offline provider recognizes a planning request by this marker, so
# planning works with no API key. Any provider may look for it; none has to.
PLAN_MARKER = "<<ai-os-plan-request>>"
# Marks the approved plan inside the executing model's system prompt, so a
# provider can walk the steps itself if it does not reason about prose.
PLAN_CONTEXT_MARKER = "<<ai-os-approved-plan>>"

PLANNER_SYSTEM_PROMPT = f"""You are the planner for AI OS, a control layer for a
Windows 11 machine. {PLAN_MARKER}

Break the user's request into the smallest ordered list of concrete steps that
satisfies it, using only the capabilities listed below. Rules:
- One action per step. Do not invent capabilities that are not listed.
- If the request is really a single action, return exactly one step.
- If part of the request cannot be done with the listed capabilities, say so in
  "notes" instead of inventing a step for it.
- Keep each description short and concrete, in the user's terms.

Reply with JSON only, in this exact shape:
{{"steps": [{{"description": "...", "action": "tool_name_or_null"}}],
  "notes": "anything the user should know, or an empty string"}}
"""

# Phrases that join two requests into one. A request containing any of these is
# worth planning; a plain "delete a.txt" is not.
_COMPOUND_MARKERS = (
    " and then ", " then ", ", then ", " after that ", " followed by ",
    " and also ", " as well as ", " plus ",
)
# Requests that are compound by nature even without a joining word.
_COMPOUND_PHRASES = (
    "prepare my", "set up my", "get my", "clean up my", "tidy up my",
    "sort out my", "ready for", "for development", "morning routine",
    "shut everything", "wrap up",
)


@dataclass
class PlanStep:
    number: int
    description: str
    action: str | None = None

    def render(self) -> str:
        suffix = f"  [{self.action}]" if self.action else ""
        return f"{self.number}. {self.description}{suffix}"


@dataclass
class Plan:
    request: str
    steps: list[PlanStep] = field(default_factory=list)
    notes: str = ""

    def __bool__(self) -> bool:
        return bool(self.steps)

    @property
    def multi_step(self) -> bool:
        return len(self.steps) > 1

    def render(self) -> str:
        lines = [step.render() for step in self.steps]
        if self.notes:
            lines.append(f"Note: {self.notes}")
        return "\n".join(lines)

    def as_context(self) -> str:
        """The approved plan, phrased for the executing model's system prompt."""
        body = "\n".join(step.render() for step in self.steps)
        return (f"{PLAN_CONTEXT_MARKER} The user approved this plan. Carry it out "
                f"in order, one tool call per step:\n{body}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "notes": self.notes,
            "steps": [
                {"number": step.number, "description": step.description,
                 "action": step.action}
                for step in self.steps
            ],
        }


def looks_compound(request: str) -> bool:
    """Cheap gate: is this worth spending a planning call on?"""
    text = f" {request.strip().lower()} "
    if any(marker in text for marker in _COMPOUND_MARKERS):
        return True
    if any(phrase in text for phrase in _COMPOUND_PHRASES):
        return True
    # "delete a.txt, b.txt and c.txt" is one action repeated, not a plan; but
    # two distinct verbs separated by a comma usually is.
    return len(re.findall(r"\b(?:open|close|delete|move|rename|organize|lock|"
                          r"shut down|restart|sleep|schedule|remind)\b", text)) > 1


class Planner:
    """Turns a compound request into an ordered, reviewable plan."""

    def __init__(self, max_steps: int = 12) -> None:
        self.max_steps = max_steps

    def plan(self, request: str, provider: Provider,
             capabilities: list[dict[str, Any]]) -> Plan:
        """Ask the model for a plan. Returns an empty Plan if it declines."""
        catalogue = "\n".join(
            f"- {tool['name']}: {tool['description']}" for tool in capabilities
        )
        messages = [
            Message("system", PLANNER_SYSTEM_PROMPT + "\nCapabilities:\n" + catalogue),
            Message("user", request),
        ]
        # No tools: the planner writes a plan, it does not act.
        response = provider.chat(messages, [])
        return self.parse(request, response.text)

    def parse(self, request: str, text: str) -> Plan:
        payload = _extract_json(text)
        if payload is None:
            return Plan(request=request, notes=text.strip()[:500])

        steps: list[PlanStep] = []
        for raw in payload.get("steps", [])[: self.max_steps]:
            if isinstance(raw, str):
                description, action = raw, None
            elif isinstance(raw, dict):
                description = str(raw.get("description") or "").strip()
                action = raw.get("action") or None
                if action in ("null", "none", ""):
                    action = None
            else:
                continue
            if description:
                steps.append(PlanStep(len(steps) + 1, description, action))
        return Plan(request=request, steps=steps,
                    notes=str(payload.get("notes") or "").strip())


def _extract_json(text: str) -> dict[str, Any] | None:
    """Models wrap JSON in prose or fences often enough to be worth handling."""
    if not text:
        return None
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        candidates.insert(0, fenced.group(1))
    braced = re.search(r"\{.*\}", text, re.S)
    if braced:
        candidates.append(braced.group(0))
    for candidate in candidates:
        try:
            payload = json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


__all__ = [
    "LLMError",
    "PLAN_CONTEXT_MARKER",
    "PLAN_MARKER",
    "Plan",
    "PlanStep",
    "Planner",
    "looks_compound",
    "parse_context_steps",
]


_STEP_LINE = re.compile(r"^\s*(\d+)\.\s+(.*?)(?:\s+\[(\w+)\])?\s*$")


def parse_context_steps(text: str) -> list[PlanStep]:
    """Read back the steps from a rendered plan context.

    Providers that do not reason over prose can use this to walk an approved
    plan one step at a time.
    """
    steps: list[PlanStep] = []
    for line in text.splitlines():
        match = _STEP_LINE.match(line)
        if match:
            steps.append(PlanStep(int(match.group(1)), match.group(2).strip(),
                                  match.group(3)))
    return steps
