"""The supervisor: proposal -> decision -> approval -> execution -> audit."""

from __future__ import annotations

from typing import Any

from ai_os.agent import ApprovalResponse, AutoApprover, RejectAllApprover
from ai_os.llm.base import LLMError, LLMResponse, Message, Provider, ToolCall


class ScriptedProvider(Provider):
    """Returns a queued list of responses, one per chat() call."""

    name = "scripted"
    requires_api_key = False

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(model="scripted-1")
        self.responses = list(responses)
        self.calls: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append(list(messages))
        if not self.responses:
            return LLMResponse(text="done")
        return self.responses.pop(0)


class RecordingApprover:
    def __init__(self, approved: bool, args: dict[str, Any] | None = None) -> None:
        self.approved = approved
        self.args = args
        self.seen: list[Any] = []

    def review(self, proposal, dry_run_result=None):
        self.seen.append((proposal, dry_run_result))
        return ApprovalResponse(self.approved, self.args)


def use_provider(app, provider):
    app.supervisor.router.build = lambda name=None: provider  # type: ignore[assignment]
    return provider


def tool_turn(name: str, arguments: dict[str, Any]) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(id="call-1", name=name, arguments=arguments)])


def test_read_only_action_runs_without_approval(app):
    use_provider(app, ScriptedProvider([tool_turn("system_time", {}),
                                        LLMResponse(text="Told you the time.")]))
    approver = RecordingApprover(True)
    turn = app.supervisor.handle("what time is it", approver)
    assert [o.status for o in turn.outcomes] == ["executed"]
    assert approver.seen == []


def test_rejecting_a_proposal_changes_nothing(app, home):
    target = home / "junk.txt"
    target.write_text("keep me")
    use_provider(app, ScriptedProvider([tool_turn("fs_delete", {"path": str(target)})]))
    turn = app.supervisor.handle("delete junk.txt", RejectAllApprover())
    assert [o.status for o in turn.outcomes] == ["rejected"]
    assert target.exists()


def test_approving_a_delete_executes_and_records_undo(app, home):
    target = home / "junk.txt"
    target.write_text("bye")
    use_provider(app, ScriptedProvider([tool_turn("fs_delete", {"path": str(target)})]))
    turn = app.supervisor.handle("delete junk.txt", AutoApprover())
    outcome = turn.outcomes[0]
    assert outcome.status == "executed"
    assert not target.exists()
    assert outcome.undo_id is not None

    undone = app.undo.undo(outcome.undo_id)
    assert undone.result.ok
    assert target.read_text() == "bye"


def test_editing_arguments_at_approval_time_is_honoured(app, home):
    (home / "a.txt").write_text("a")
    (home / "b.txt").write_text("b")
    use_provider(app, ScriptedProvider([tool_turn("fs_delete", {"path": str(home / "a.txt")})]))
    approver = RecordingApprover(True, {"path": str(home / "b.txt")})
    turn = app.supervisor.handle("delete a.txt", approver)
    assert turn.outcomes[0].status == "executed"
    assert (home / "a.txt").exists()
    assert not (home / "b.txt").exists()


def test_edits_cannot_escape_the_whitelist(app, home, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("safe")
    (home / "a.txt").write_text("a")
    use_provider(app, ScriptedProvider([tool_turn("fs_delete", {"path": str(home / "a.txt")})]))
    approver = RecordingApprover(True, {"path": str(outside)})
    turn = app.supervisor.handle("delete a.txt", approver)
    assert turn.outcomes[0].status == "denied"
    assert outside.exists()


def test_dry_run_never_mutates(app, home):
    (home / "a.png").write_text("x")
    use_provider(app, ScriptedProvider([tool_turn("fs_organize", {"path": str(home)})]))
    turn = app.supervisor.handle("organize my home folder", AutoApprover(), dry_run=True)
    assert turn.outcomes[0].status == "dry-run"
    assert (home / "a.png").exists()
    assert not (home / "Images").exists()


def test_bulk_actions_are_previewed_before_approval(app, home):
    (home / "a.png").write_text("x")
    use_provider(app, ScriptedProvider([tool_turn("fs_organize", {"path": str(home)})]))
    approver = RecordingApprover(True)
    app.supervisor.handle("organize my home folder", approver)
    _, preview = approver.seen[0]
    assert preview is not None and preview.data["dry_run"]


def test_unknown_tool_names_are_denied_not_executed(app):
    use_provider(app, ScriptedProvider([tool_turn("format_c_drive", {})]))
    turn = app.supervisor.handle("do something odd", AutoApprover())
    assert turn.outcomes[0].status == "denied"


def test_invalid_arguments_are_denied(app):
    use_provider(app, ScriptedProvider([tool_turn("fs_rename", {"path": "/tmp/a"})]))
    turn = app.supervisor.handle("rename something", AutoApprover())
    assert turn.outcomes[0].status == "denied"


def test_provider_errors_surface_without_crashing(app):
    class BrokenProvider(Provider):
        name = "broken"
        requires_api_key = False

        def chat(self, messages, tools):
            raise LLMError("no API key configured")

    use_provider(app, BrokenProvider())
    turn = app.supervisor.handle("hello", AutoApprover())
    assert turn.error and "no API key" in turn.error
    assert turn.outcomes == []


def test_a_failing_skill_is_recorded_not_raised(app, home):
    use_provider(app, ScriptedProvider([
        tool_turn("fs_rename", {"path": str(home / "missing.txt"), "new_name": "x.txt"})]))
    turn = app.supervisor.handle("rename missing", AutoApprover())
    assert turn.outcomes[0].status == "failed"


def test_every_decision_reaches_the_audit_log(app, home):
    (home / "a.txt").write_text("a")
    use_provider(app, ScriptedProvider([tool_turn("fs_delete", {"path": str(home / "a.txt")})]))
    app.supervisor.handle("delete a.txt", RejectAllApprover())
    rows = app.audit.recent_actions(5)
    assert rows[0]["outcome"] == "rejected"
    assert rows[0]["risk"] == "destructive"
    assert rows[0]["verdict"] == "approve"
    requests = app.audit.recent_requests(5)
    assert requests[0]["text"] == "delete a.txt"


def test_prompt_logging_can_be_turned_off(app, home):
    app.config.log_prompts = False
    use_provider(app, ScriptedProvider([LLMResponse(text="ok")]))
    app.supervisor.handle("something private", AutoApprover())
    assert "private" not in app.audit.recent_requests(1)[0]["text"]


def test_tool_results_are_fed_back_to_the_model(app):
    provider = use_provider(app, ScriptedProvider([
        tool_turn("system_time", {}),
        LLMResponse(text="It is time."),
    ]))
    turn = app.supervisor.handle("what time is it", AutoApprover())
    assert turn.reply == "It is time."
    last_messages = provider.calls[-1]
    assert last_messages[-1].role == "tool"
    assert "executed" in last_messages[-1].content


def test_the_tool_loop_is_bounded(app):
    app.config.max_tool_iterations = 2
    provider = use_provider(app, ScriptedProvider(
        [tool_turn("system_time", {}) for _ in range(10)]))
    app.supervisor.handle("loop forever", AutoApprover())
    assert len(provider.calls) == 2
