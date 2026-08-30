"""The planner turns compound requests into a reviewable, ordered plan."""

from __future__ import annotations

import pytest

from ai_os.agent import ApprovalResponse, AutoApprover, PlanResponse
from ai_os.llm.base import LLMError, LLMResponse, Message, Provider, ToolCall
from ai_os.llm.local import LocalRuleProvider, split_request
from ai_os.planner import (
    PLAN_CONTEXT_MARKER,
    PLANNER_SYSTEM_PROMPT,
    Plan,
    Planner,
    looks_compound,
    parse_context_steps,
)
from ai_os.skills import default_registry


@pytest.mark.parametrize("request_text", [
    "open chrome and then lock my pc",
    "prepare my laptop for development",
    "organize my desktop, then delete downloads/old.txt",
    "clean up my downloads and also open chrome",
])
def test_compound_requests_are_worth_planning(request_text):
    assert looks_compound(request_text)


@pytest.mark.parametrize("request_text", [
    "delete a.txt",
    "what time is it",
    "organize my desktop",
])
def test_simple_requests_skip_the_planner(request_text):
    assert not looks_compound(request_text)


def test_planner_parses_a_json_plan():
    plan = Planner().parse("do things", '{"steps": ['
                           '{"description": "Open Chrome", "action": "app_open"},'
                           '{"description": "Lock the PC", "action": "power_lock"}],'
                           '"notes": "careful"}')
    assert [step.number for step in plan.steps] == [1, 2]
    assert plan.steps[0].action == "app_open"
    assert plan.notes == "careful"
    assert plan.multi_step


def test_planner_reads_json_out_of_a_fenced_block():
    text = 'Here is the plan:\n```json\n{"steps": ["Lock the PC"], "notes": ""}\n```'
    plan = Planner().parse("lock up", text)
    assert [step.description for step in plan.steps] == ["Lock the PC"]


def test_planner_keeps_prose_as_a_note_when_there_is_no_json():
    plan = Planner().parse("do things", "I cannot do that.")
    assert not plan.steps
    assert "cannot" in plan.notes


def test_planner_respects_the_step_limit():
    steps = [{"description": f"step {n}"} for n in range(30)]
    plan = Planner(max_steps=5).parse("many", __import__("json").dumps({"steps": steps}))
    assert len(plan.steps) == 5


def test_plan_context_round_trips_through_the_prompt():
    plan = Planner().parse("x", '{"steps": ['
                           '{"description": "Open Chrome", "action": "app_open"},'
                           '{"description": "Lock it", "action": "power_lock"}]}')
    context = plan.as_context()
    assert PLAN_CONTEXT_MARKER in context
    recovered = parse_context_steps(context)
    assert [step.description for step in recovered] == ["Open Chrome", "Lock it"]
    assert [step.action for step in recovered] == ["app_open", "power_lock"]


def test_offline_provider_plans_by_splitting_the_sentence():
    tools = default_registry().tool_schemas()
    response = LocalRuleProvider().chat([
        Message("system", PLANNER_SYSTEM_PROMPT),
        Message("user", "open chrome and then lock my pc"),
    ], tools)
    plan = Planner().parse("open chrome and then lock my pc", response.text)
    assert [step.action for step in plan.steps] == ["app_open", "power_lock"]


def test_offline_planner_flags_what_it_could_not_match():
    tools = default_registry().tool_schemas()
    response = LocalRuleProvider().chat([
        Message("system", PLANNER_SYSTEM_PROMPT),
        Message("user", "lock my pc and then write me a poem"),
    ], tools)
    plan = Planner().parse("x", response.text)
    assert [step.action for step in plan.steps] == ["power_lock"]
    assert "poem" in plan.notes


def test_split_request_handles_several_conjunctions():
    assert split_request("open chrome and then lock my pc") == \
        ["open chrome", "lock my pc"]
    assert len(split_request("a, then b and also c")) == 3


# -- end to end through the supervisor ---------------------------------
class PlanThenActProvider(Provider):
    """Plans on the first call, then returns one tool call per step."""

    name = "scripted"
    requires_api_key = False

    def __init__(self, plan_json: str, calls: list[ToolCall]) -> None:
        super().__init__(model="scripted-1")
        self.plan_json = plan_json
        self.calls = list(calls)
        self.saw_plan_context = False

    def chat(self, messages, tools):
        if any(PLANNER_SYSTEM_PROMPT[:40] in m.content for m in messages
               if m.role == "system"):
            return LLMResponse(text=self.plan_json)
        if any(PLAN_CONTEXT_MARKER in m.content for m in messages if m.role == "system"):
            self.saw_plan_context = True
        if self.calls:
            return LLMResponse(tool_calls=[self.calls.pop(0)])
        return LLMResponse(text="All done.")


class PlanRecordingApprover:
    def __init__(self, approved: bool, steps: list[str] | None = None) -> None:
        self.approved = approved
        self.steps = steps
        self.plans: list[Plan] = []

    def review_plan(self, plan):
        self.plans.append(plan)
        return PlanResponse(self.approved, self.steps)

    def review(self, proposal, dry_run_result=None):
        return ApprovalResponse(True)


PLAN_JSON = ('{"steps": [{"description": "Check the time", "action": "system_time"},'
             '{"description": "Check the disk", "action": "system_disk_usage"}]}')


def use_provider(app, provider):
    app.supervisor.router.build = lambda name=None: provider
    return provider


def test_an_approved_plan_runs_every_step(app, home):
    provider = use_provider(app, PlanThenActProvider(PLAN_JSON, [
        ToolCall("c1", "system_time", {}),
        ToolCall("c2", "system_disk_usage", {"path": str(home)}),
    ]))
    approver = PlanRecordingApprover(True)
    turn = app.supervisor.handle("tell me the time and then check the disk", approver)

    assert len(approver.plans) == 1
    assert turn.plan is not None and len(turn.plan.steps) == 2
    assert [o.proposal.action.name for o in turn.outcomes] == \
        ["system_time", "system_disk_usage"]
    assert provider.saw_plan_context


def test_rejecting_a_plan_runs_nothing(app):
    use_provider(app, PlanThenActProvider(PLAN_JSON, [ToolCall("c1", "system_time", {})]))
    turn = app.supervisor.handle("tell me the time and then check the disk",
                                 PlanRecordingApprover(False))
    assert turn.outcomes == []
    assert "rejected" in turn.reply.lower()


def test_an_edited_plan_replaces_the_steps(app):
    use_provider(app, PlanThenActProvider(PLAN_JSON, [ToolCall("c1", "system_time", {})]))
    approver = PlanRecordingApprover(True, steps=["Just check the time"])
    turn = app.supervisor.handle("tell me the time and then check the disk", approver)
    assert [step.description for step in turn.plan.steps] == ["Just check the time"]


def test_dry_run_skips_plan_approval_because_nothing_changes(app):
    use_provider(app, PlanThenActProvider(PLAN_JSON, [ToolCall("c1", "system_time", {})]))
    approver = PlanRecordingApprover(False)
    turn = app.supervisor.handle("tell me the time and then check the disk", approver,
                                 dry_run=True)
    assert approver.plans == []
    assert turn.plan is not None


def test_planning_can_be_turned_off(app):
    app.config.planner_enabled = False
    provider = use_provider(app, PlanThenActProvider(
        PLAN_JSON, [ToolCall("c1", "system_time", {})]))
    turn = app.supervisor.handle("tell me the time and then check the disk",
                                 AutoApprover())
    assert turn.plan is None
    assert not provider.saw_plan_context


def test_a_planning_failure_does_not_stop_the_request(app):
    class PlanFailsProvider(Provider):
        name = "scripted"
        requires_api_key = False

        def chat(self, messages, tools):
            if any(PLANNER_SYSTEM_PROMPT[:40] in m.content for m in messages
                   if m.role == "system"):
                raise LLMError("planner unavailable")
            return LLMResponse(tool_calls=[ToolCall("c1", "system_time", {})])

    use_provider(app, PlanFailsProvider())
    turn = app.supervisor.handle("tell me the time and then check the disk",
                                 AutoApprover())
    assert turn.outcomes[0].status == "executed"
    assert "planner unavailable" in (turn.error or "")


def test_an_approver_without_plan_review_still_works(app):
    """The plan hook is optional; a plain approver must not break."""
    use_provider(app, PlanThenActProvider(PLAN_JSON, [ToolCall("c1", "system_time", {})]))
    turn = app.supervisor.handle("tell me the time and then check the disk",
                                 AutoApprover())
    assert turn.outcomes[0].status == "executed"
