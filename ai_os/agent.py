"""The agent supervisor.

Takes a natural-language request, asks the configured model what to do, routes
each proposed action through the safety engine, collects approvals, executes
what was approved, and records everything in the audit log.

The interface (CLI or GUI) supplies an Approver; the supervisor itself never
prompts, which is what lets the same loop drive a terminal and a window.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .audit import AuditLog
from .config import Config
from .llm import LLMError, LLMRouter, Message, ToolCall
from .safety import Decision, SafetyEngine, Verdict
from .skills.base import ActionDef, ActionResult, ExecContext, RiskLevel
from .skills.registry import SkillRegistry

SYSTEM_PROMPT = """You are AI OS, a control layer for a Windows 11 machine.

Turn the user's request into calls to the tools you have been given. Rules:
- Only use the provided tools; never claim to have done something you did not do.
- Prefer the least destructive tool that satisfies the request.
- Ask a clarifying question in plain text instead of guessing at a destructive
  action (deleting, closing an app, shutting down).
- Every risky action is reviewed by the user before it runs, so propose the
  action rather than describing how the user could do it themselves.
- When tool results come back, reply with a short, plain summary of what
  happened. Do not invent details that are not in the results.
"""


@dataclass
class Proposal:
    """One tool call, together with the safety engine's decision about it."""

    call_id: str
    action: ActionDef
    args: dict[str, Any]
    decision: Decision
    error: str | None = None

    @property
    def summary(self) -> str:
        if self.error:
            return f"{self.action.name if self.action else 'unknown'}: {self.error}"
        return self.action.describe(self.args)

    def with_args(self, args: dict[str, Any]) -> Proposal:
        return Proposal(self.call_id, self.action, args, self.decision)


@dataclass
class Outcome:
    """What happened to one proposal."""

    proposal: Proposal
    status: str           # executed | dry-run | rejected | denied | failed
    result: ActionResult | None = None
    undo_id: int | None = None

    @property
    def message(self) -> str:
        if self.result:
            return self.result.message
        return {
            "rejected": "Rejected by the user.",
            "denied": "Blocked by the permission rules.",
        }.get(self.status, self.status)


@dataclass
class TurnResult:
    request: str
    reply: str = ""
    outcomes: list[Outcome] = field(default_factory=list)
    request_id: int | None = None
    provider: str = ""
    model: str = ""
    error: str | None = None


@dataclass
class ApprovalResponse:
    """What an interface hands back when asked to review a proposal."""

    approved: bool
    args: dict[str, Any] | None = None    # edited arguments, when the user changed them
    note: str = ""


class Approver(Protocol):
    """Anything that can review a proposal: a prompt, a GUI queue, or a policy."""

    def review(self, proposal: Proposal, dry_run_result: ActionResult | None) \
        -> ApprovalResponse: ...  # pragma: no cover - interface


class AutoApprover:
    """Approves everything. For tests and explicit unattended runs only."""

    def review(self, proposal: Proposal,
               dry_run_result: ActionResult | None = None) -> ApprovalResponse:
        return ApprovalResponse(True)


class RejectAllApprover:
    """Rejects everything; useful for previewing a request end to end."""

    def review(self, proposal: Proposal,
               dry_run_result: ActionResult | None = None) -> ApprovalResponse:
        return ApprovalResponse(False, note="auto-rejected")


class Supervisor:
    """Drives one request from natural language to executed, audited actions."""

    def __init__(self, config: Config, registry: SkillRegistry, audit: AuditLog,
                 router: LLMRouter | None = None,
                 safety: SafetyEngine | None = None) -> None:
        self.config = config
        self.registry = registry
        self.audit = audit
        self.router = router or LLMRouter(config)
        self.safety = safety or SafetyEngine(config)

    def handle(self, request: str, approver: Approver, *, dry_run: bool | None = None,
               interface: str = "cli",
               on_event: Callable[[str, Any], None] | None = None) -> TurnResult:
        dry_run = self.config.dry_run_default if dry_run is None else dry_run
        provider = self.router.build()
        turn = TurnResult(request=request, provider=provider.name,
                          model=provider.model, error=None)
        turn.request_id = self.audit.start_request(
            request if self.config.log_prompts else "(prompt logging disabled)",
            interface=interface, provider=provider.name, model=provider.model,
            dry_run=bool(dry_run),
        )

        messages: list[Message] = [
            Message("system", SYSTEM_PROMPT),
            Message("user", request),
        ]
        tools = self.registry.tool_schemas()

        for _ in range(max(1, self.config.max_tool_iterations)):
            try:
                response = provider.chat(messages, tools)
            except LLMError as exc:
                turn.error = str(exc)
                turn.reply = f"Model error: {exc}"
                break

            if not response.tool_calls:
                turn.reply = response.text
                break

            messages.append(Message("assistant", response.text,
                                    tool_calls=response.tool_calls))
            for call in response.tool_calls:
                proposal = self._prepare(call)
                outcome = self._resolve(proposal, approver, dry_run, turn, on_event)
                turn.outcomes.append(outcome)
                messages.append(Message(
                    "tool",
                    content=_tool_result_text(outcome),
                    tool_call_id=call.id,
                ))
        else:
            turn.reply = turn.reply or "Stopped after the maximum number of tool steps."

        if not turn.reply and turn.outcomes:
            turn.reply = "; ".join(o.message for o in turn.outcomes)
        self.audit.finish_request(turn.request_id, turn.reply)
        return turn

    # -- internals ------------------------------------------------------
    def _prepare(self, call: ToolCall) -> Proposal:
        action = self.registry.get(call.name)
        if action is None:
            unknown = _unknown_action(call.name)
            return Proposal(call.id, unknown, dict(call.arguments),
                            Decision(Verdict.DENY, unknown.risk,
                                     [f"unknown action {call.name!r}"]),
                            error=f"unknown action {call.name!r}")
        try:
            args = action.validate(dict(call.arguments))
        except ValueError as exc:
            return Proposal(call.id, action, dict(call.arguments),
                            Decision(Verdict.DENY, action.risk, [str(exc)]),
                            error=str(exc))
        return Proposal(call.id, action, args, self.safety.evaluate(action, args))

    def _resolve(self, proposal: Proposal, approver: Approver, dry_run: bool,
                 turn: TurnResult,
                 on_event: Callable[[str, Any], None] | None) -> Outcome:
        emit = on_event or (lambda *_: None)
        emit("proposal", proposal)

        if proposal.decision.verdict is Verdict.DENY:
            return self._record(turn, proposal, "denied", None, dry_run)

        preview: ActionResult | None = None
        needs_preview = proposal.decision.force_dry_run or dry_run
        if needs_preview:
            preview = self._execute(proposal, dry_run=True)
            emit("preview", preview)

        if dry_run:
            # Dry-run mode never mutates, whatever the verdict would have been.
            return self._record(turn, proposal, "dry-run", preview, True)

        if proposal.decision.verdict is Verdict.APPROVE:
            response = approver.review(proposal, preview)
            if not response.approved:
                return self._record(turn, proposal, "rejected", None, dry_run)
            if response.args is not None and response.args != proposal.args:
                try:
                    edited = proposal.action.validate(dict(response.args))
                except ValueError as exc:
                    proposal.error = str(exc)
                    return self._record(turn, proposal, "failed", None, dry_run)
                proposal = proposal.with_args(edited)
                # Re-check the edited arguments; editing must not bypass safety.
                proposal.decision = self.safety.evaluate(proposal.action, edited)
                if proposal.decision.verdict is Verdict.DENY:
                    return self._record(turn, proposal, "denied", None, dry_run)

        result = self._execute(proposal, dry_run=False)
        status = "executed" if result.ok else "failed"
        return self._record(turn, proposal, status, result, dry_run)

    def _execute(self, proposal: Proposal, *, dry_run: bool) -> ActionResult:
        ctx = ExecContext(dry_run=dry_run, config=self.config)
        try:
            return proposal.action.handler(proposal.args, ctx)
        except Exception as exc:  # a skill must never take the whole app down
            return ActionResult(False, f"{proposal.action.name} failed: {exc}")

    def _record(self, turn: TurnResult, proposal: Proposal, status: str,
                result: ActionResult | None, dry_run: bool) -> Outcome:
        message = result.message if result else (proposal.error or status)
        action_id = self.audit.record_action(
            request_id=turn.request_id,
            action=proposal.action.name,
            skill=proposal.action.skill,
            arguments=proposal.args,
            summary=proposal.summary,
            risk=proposal.decision.risk.label,
            verdict=proposal.decision.verdict.value,
            decision_note=proposal.decision.explain(),
            outcome=status,
            message=message,
            dry_run=bool(dry_run or status == "dry-run"),
        )
        undo_id = None
        if result and result.undo and status == "executed":
            undo_id = self.audit.push_undo(
                action_id, result.undo.kind, result.undo.payload,
                result.undo.description or proposal.summary,
            )
        return Outcome(proposal, status, result, undo_id)


def _tool_result_text(outcome: Outcome) -> str:
    payload: dict[str, Any] = {"status": outcome.status, "message": outcome.message}
    if outcome.result and outcome.result.data:
        payload["data"] = outcome.result.data
    return json.dumps(payload, default=str)[:4000]


def _unknown_action(name: str) -> ActionDef:
    """A stand-in so an unrecognized tool name still flows through audit."""
    return ActionDef(
        name=name,
        skill="unknown",
        description="Unknown action proposed by the model.",
        risk=RiskLevel.DESTRUCTIVE,
        handler=lambda args, ctx: ActionResult(False, f"unknown action {name!r}"),
    )
