"""The GUI approval queue.

The supervisor runs on a worker thread and blocks on this queue whenever an
action needs approval; the window shows the request, and the user's answer
releases the worker.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any

from ..agent import ApprovalResponse, PlanResponse, Proposal
from ..planner import Plan
from ..skills.base import ActionResult


@dataclass
class PendingApproval:
    proposal: Proposal
    preview: ActionResult | None
    done: threading.Event = field(default_factory=threading.Event)
    response: ApprovalResponse | None = None

    def resolve(self, approved: bool, args: dict[str, Any] | None = None,
                note: str = "") -> None:
        self.response = ApprovalResponse(approved, args, note)
        self.done.set()


@dataclass
class PendingPlan:
    """A multi-step plan waiting for the user to approve it as a whole."""

    plan: Plan
    done: threading.Event = field(default_factory=threading.Event)
    response: PlanResponse | None = None

    def resolve(self, approved: bool, steps: list[str] | None = None,
                note: str = "") -> None:
        self.response = PlanResponse(approved, steps, note)
        self.done.set()


class QueueApprover:
    """Approver implementation backed by a thread-safe queue."""

    def __init__(self, timeout: float = 300.0) -> None:
        self.queue: queue.Queue[PendingApproval | PendingPlan] = queue.Queue()
        self.timeout = timeout

    def review(self, proposal: Proposal,
               dry_run_result: ActionResult | None = None) -> ApprovalResponse:
        pending = PendingApproval(proposal, dry_run_result)
        self.queue.put(pending)
        if not pending.done.wait(self.timeout):
            return ApprovalResponse(False, note="approval timed out")
        return pending.response or ApprovalResponse(False, note="no response")

    def review_plan(self, plan: Plan) -> PlanResponse:
        """Block the worker thread until the window answers about the plan."""
        pending = PendingPlan(plan)
        self.queue.put(pending)
        if not pending.done.wait(self.timeout):
            return PlanResponse(False, note="plan approval timed out")
        return pending.response or PlanResponse(False, note="no response")

    def next_pending(self) -> PendingApproval | PendingPlan | None:
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None
