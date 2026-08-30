"""The GUI approval queue — the part of the window that has no Tk dependency."""

from __future__ import annotations

import threading

from ai_os.gui.approval import QueueApprover
from ai_os.llm.base import LLMResponse, Message, Provider, ToolCall


class OneDeleteProvider(Provider):
    name = "scripted"
    requires_api_key = False

    def __init__(self, path: str) -> None:
        super().__init__(model="scripted-1")
        self.path = path
        self.turns = 0

    def chat(self, messages: list[Message], tools) -> LLMResponse:
        self.turns += 1
        if self.turns == 1:
            return LLMResponse(tool_calls=[
                ToolCall("c1", "fs_delete", {"path": self.path})])
        return LLMResponse(text="Done.")


def run_in_background(app, request, approver):
    result: dict = {}

    def worker():
        result["turn"] = app.supervisor.handle(request, approver, interface="gui")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, result


def test_the_queue_hands_a_proposal_to_the_ui_and_waits(app, home):
    target = home / "junk.txt"
    target.write_text("bye")
    app.supervisor.router.build = lambda name=None: OneDeleteProvider(str(target))

    approver = QueueApprover(timeout=10)
    thread, result = run_in_background(app, "delete junk.txt", approver)

    pending = approver.queue.get(timeout=5)
    assert pending.proposal.action.name == "fs_delete"
    assert target.exists(), "nothing should run before the user answers"

    pending.resolve(True)
    thread.join(timeout=5)
    assert not target.exists()
    assert result["turn"].outcomes[0].status == "executed"


def test_rejecting_from_the_queue_leaves_the_file_alone(app, home):
    target = home / "junk.txt"
    target.write_text("keep")
    app.supervisor.router.build = lambda name=None: OneDeleteProvider(str(target))

    approver = QueueApprover(timeout=10)
    thread, result = run_in_background(app, "delete junk.txt", approver)
    pending = approver.queue.get(timeout=5)
    pending.resolve(False)
    thread.join(timeout=5)

    assert target.exists()
    assert result["turn"].outcomes[0].status == "rejected"


def test_an_unanswered_approval_times_out_as_a_rejection(app, home):
    target = home / "junk.txt"
    target.write_text("keep")
    app.supervisor.router.build = lambda name=None: OneDeleteProvider(str(target))

    approver = QueueApprover(timeout=0.2)
    turn = app.supervisor.handle("delete junk.txt", approver, interface="gui")
    assert turn.outcomes[0].status == "rejected"
    assert target.exists()


def test_editing_through_the_queue_passes_new_arguments(app, home):
    (home / "a.txt").write_text("a")
    (home / "b.txt").write_text("b")
    app.supervisor.router.build = lambda name=None: OneDeleteProvider(str(home / "a.txt"))

    approver = QueueApprover(timeout=10)
    thread, result = run_in_background(app, "delete a.txt", approver)
    pending = approver.queue.get(timeout=5)
    pending.resolve(True, {"path": str(home / "b.txt")}, note="edited")
    thread.join(timeout=5)

    assert (home / "a.txt").exists()
    assert not (home / "b.txt").exists()


# -- plan review through the queue -------------------------------------
class PlanThenLockProvider(Provider):
    """Plans two steps, then works through whichever steps were approved.

    Following the approved plan rather than a fixed script is what a real model
    does, and it is what makes an edited plan observable.
    """

    name = "scripted"
    requires_api_key = False

    def __init__(self, path: str) -> None:
        super().__init__(model="scripted-1")
        self.path = path

    def chat(self, messages, tools):
        from ai_os.planner import PLAN_CONTEXT_MARKER, PLAN_MARKER, parse_context_steps

        if any(PLAN_MARKER in m.content for m in messages if m.role == "system"):
            return LLMResponse(text='{"steps": ['
                                    '{"description": "Check the time", '
                                    '"action": "system_time"},'
                                    '{"description": "Delete the file", '
                                    '"action": "fs_delete"}]}')

        context = next((m.content for m in messages
                        if m.role == "system" and PLAN_CONTEXT_MARKER in m.content), "")
        steps = parse_context_steps(context)
        done = sum(1 for message in messages if message.role == "tool")
        if done < len(steps):
            action = steps[done].action
            arguments = {"path": self.path} if action == "fs_delete" else {}
            return LLMResponse(tool_calls=[
                ToolCall(f"c{done + 1}", action, arguments)])
        return LLMResponse(text="Done.")


def test_a_plan_reaches_the_window_before_any_step_runs(app, home):
    from ai_os.gui.approval import PendingPlan

    target = home / "junk.txt"
    target.write_text("bye")
    app.supervisor.router.build = lambda name=None: PlanThenLockProvider(str(target))

    approver = QueueApprover(timeout=10)
    thread, result = run_in_background(app, "tell me the time and then delete junk.txt",
                                       approver)

    pending = approver.queue.get(timeout=5)
    assert isinstance(pending, PendingPlan)
    assert len(pending.plan.steps) == 2
    assert target.exists(), "no step should run before the plan is approved"

    pending.resolve(True)
    # Risky steps are still reviewed individually; the read-only step is not.
    approver.queue.get(timeout=5).resolve(True)
    thread.join(timeout=5)

    assert not target.exists()
    assert [o.status for o in result["turn"].outcomes] == ["executed", "executed"]


def test_rejecting_a_plan_from_the_window_runs_nothing(app, home):
    target = home / "junk.txt"
    target.write_text("keep")
    app.supervisor.router.build = lambda name=None: PlanThenLockProvider(str(target))

    approver = QueueApprover(timeout=10)
    thread, result = run_in_background(app, "tell me the time and then delete junk.txt",
                                       approver)
    approver.queue.get(timeout=5).resolve(False)
    thread.join(timeout=5)

    assert target.exists()
    assert result["turn"].outcomes == []


def test_an_unanswered_plan_times_out_as_a_rejection(app, home):
    target = home / "junk.txt"
    target.write_text("keep")
    app.supervisor.router.build = lambda name=None: PlanThenLockProvider(str(target))

    approver = QueueApprover(timeout=0.2)
    turn = app.supervisor.handle("tell me the time and then delete junk.txt",
                                 approver, interface="gui")
    assert turn.outcomes == []
    assert target.exists()


def test_editing_a_plan_through_the_queue_replaces_the_steps(app, home):
    target = home / "junk.txt"
    target.write_text("keep")
    app.supervisor.router.build = lambda name=None: PlanThenLockProvider(str(target))

    approver = QueueApprover(timeout=10)
    thread, result = run_in_background(app, "tell me the time and then delete junk.txt",
                                       approver)
    approver.queue.get(timeout=5).resolve(True, ["Check the time"], note="edited")
    thread.join(timeout=5)

    plan = result["turn"].plan
    assert [step.description for step in plan.steps] == ["Check the time"]
