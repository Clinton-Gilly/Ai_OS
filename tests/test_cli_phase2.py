"""The Phase 2 CLI surface: plans, memory, diagnose, listen, and settings."""

from __future__ import annotations

import json

import pytest

from ai_os.cli import ConsoleApprover, main
from ai_os.planner import Plan, PlanStep


@pytest.fixture
def cli(tmp_path, home):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "audit.sqlite3"

    def run(*args: str) -> int:
        return main(["--config", str(config_path), "--db", str(db_path), *args])

    main(["--config", str(config_path), "--db", str(db_path),
          "settings", "allow-folder", str(home)])
    return run


def test_a_compound_request_shows_a_plan_and_runs_it(cli, home, capsys):
    (home / "a.png").write_text("x")
    assert cli("run", f"organize {home} and then tell me the time", "--yes") == 0
    output = capsys.readouterr().out
    assert "Plan:" in output
    assert "[OK]" in output
    assert (home / "Images" / "a.png").exists()


def test_no_plan_skips_the_planner(cli, capsys):
    assert cli("run", "tell me the time and then tell me the time", "--yes",
               "--no-plan") == 0
    assert "Plan:" not in capsys.readouterr().out


def test_plan_appears_in_json_output(cli, home, capsys):
    assert cli("run", f"organize {home} and then tell me the time", "--yes",
               "--json", "--dry-run") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan"] is not None
    assert len(payload["plan"]["steps"]) == 2


def test_memory_list_is_empty_to_begin_with(cli, capsys):
    assert cli("memory", "list") == 0
    assert "Nothing is remembered" in capsys.readouterr().out


def test_memory_remember_list_and_forget(cli, capsys):
    assert cli("memory", "remember", "preference", "browser", "firefox") == 0
    capsys.readouterr()

    assert cli("memory", "list") == 0
    assert "firefox" in capsys.readouterr().out

    assert cli("memory", "forget", "--kind", "preference", "--key", "browser") == 0
    assert "Forgot 1 item" in capsys.readouterr().out

    assert cli("memory", "list") == 0
    assert "Nothing is remembered" in capsys.readouterr().out


def test_memory_forget_needs_a_target(cli, capsys):
    assert cli("memory", "forget") == 1
    assert "Nothing selected" in capsys.readouterr().out


def test_memory_forget_key_requires_a_kind(cli, capsys):
    assert cli("memory", "forget", "--key", "browser") == 1
    assert "needs --kind" in capsys.readouterr().out


def test_memory_search_filters(cli, capsys):
    cli("memory", "remember", "fact", "work drive", "D:")
    cli("memory", "remember", "fact", "home drive", "C:")
    capsys.readouterr()
    assert cli("memory", "list", "--search", "work") == 0
    output = capsys.readouterr().out
    assert "work drive" in output and "home drive" not in output


def test_running_a_request_teaches_memory(cli, home, capsys):
    (home / "note.txt").write_text("x")
    cli("run", f"rename {home / 'note.txt'} to renamed.txt", "--yes")
    capsys.readouterr()
    cli("memory", "list", "--kind", "folder")
    assert str(home) in capsys.readouterr().out


def test_diagnose_prints_findings(cli, capsys):
    assert cli("diagnose") == 0
    assert capsys.readouterr().out.strip().startswith("-")


def test_diagnose_json_is_machine_readable(cli, capsys):
    assert cli("diagnose", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert "findings" in payload and "disks" in payload


def test_listen_says_so_when_voice_is_off(cli, capsys):
    assert cli("listen") == 1
    assert "Voice input is off" in capsys.readouterr().out


def test_listen_reports_missing_libraries(cli, capsys, monkeypatch):
    cli("settings", "set", "voice-enabled", "true")
    capsys.readouterr()
    monkeypatch.setattr(
        "ai_os.voice.SpeechRecognitionTranscriber.available", lambda self: False)
    assert cli("listen") == 1
    assert "pip install" in capsys.readouterr().out


def test_phase_two_settings_persist(cli, tmp_path):
    cli("settings", "set", "planner-enabled", "false")
    cli("settings", "set", "memory-enabled", "false")
    cli("settings", "set", "hotkey", "ctrl+shift+space")
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["planner_enabled"] is False
    assert saved["memory_enabled"] is False
    assert saved["hotkey"] == "ctrl+shift+space"


def test_new_skills_appear_in_the_skill_list(cli, capsys):
    assert cli("skills") == 0
    output = capsys.readouterr().out
    for name in ("diag_report", "schedule_reminder", "memory_remember"):
        assert name in output


# -- the terminal plan reviewer ----------------------------------------
def make_plan() -> Plan:
    return Plan(request="x", steps=[PlanStep(1, "Open Chrome", "app_open"),
                                    PlanStep(2, "Lock the PC", "power_lock")])


def test_console_plan_review_approves(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "a")
    response = ConsoleApprover().review_plan(make_plan())
    assert response.approved and response.steps is None
    assert "Open Chrome" in capsys.readouterr().out


def test_console_plan_review_rejects_on_enter(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    assert not ConsoleApprover().review_plan(make_plan()).approved


def test_console_plan_review_edits_steps(monkeypatch):
    answers = iter(["e", "Open Edge instead", "-"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    response = ConsoleApprover().review_plan(make_plan())
    assert response.approved
    assert response.steps == ["Open Edge instead"]


def test_console_plan_review_rejects_without_a_terminal(monkeypatch):
    def no_input(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_input)
    assert not ConsoleApprover().review_plan(make_plan()).approved
