"""The CLI surface: run, skills, audit, undo, and settings."""

from __future__ import annotations

import json

import pytest

from ai_os.cli import main


@pytest.fixture
def cli(tmp_path, home, monkeypatch):
    """Run the CLI against an isolated config and database."""
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "audit.sqlite3"

    def run(*args: str) -> int:
        return main(["--config", str(config_path), "--db", str(db_path), *args])

    # Start from a whitelist that covers the fake home directory.
    main(["--config", str(config_path), "--db", str(db_path),
          "settings", "allow-folder", str(home)])
    return run


def test_skills_command_lists_every_action(cli, capsys):
    assert cli("skills") == 0
    output = capsys.readouterr().out
    assert "fs_organize" in output
    assert "[destructive]" in output
    assert "actions across" in output


def test_run_dry_run_reports_without_changing_anything(cli, home, capsys):
    (home / "a.png").write_text("x")
    assert cli("run", "organize my home folder", "--dry-run") == 0
    output = capsys.readouterr().out
    assert "[DRY]" in output
    assert (home / "a.png").exists()


def test_run_with_yes_executes_and_offers_undo(cli, home, capsys):
    (home / "a.png").write_text("x")
    (home / "b.pdf").write_text("x")
    assert cli("run", f"organize {home}", "--yes") == 0
    output = capsys.readouterr().out
    assert "[OK]" in output and "ai-os undo --id" in output
    assert (home / "Images" / "a.png").exists()

    assert cli("undo") == 0
    assert (home / "a.png").exists()


def test_run_json_output_is_machine_readable(cli, home, capsys):
    assert cli("run", "what time is it", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcomes"][0]["action"] == "system_time"
    assert payload["outcomes"][0]["status"] == "executed"
    assert payload["outcomes"][0]["risk"] == "read-only"


def test_run_refuses_a_path_outside_the_whitelist(cli, tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("safe")
    assert cli("run", f"delete {outside / 'x.txt'}", "--yes") == 0
    assert "[DENY]" in capsys.readouterr().out
    assert (outside / "x.txt").exists()


def test_audit_command_shows_history(cli, capsys):
    cli("run", "what time is it", "--yes")
    capsys.readouterr()
    assert cli("audit") == 0
    output = capsys.readouterr().out
    assert "executed" in output
    assert "risk=read-only" in output


def test_audit_requests_view(cli, capsys):
    cli("run", "what time is it", "--yes")
    capsys.readouterr()
    assert cli("audit", "--requests") == 0
    assert "what time is it" in capsys.readouterr().out


def test_undo_list_is_empty_before_anything_happens(cli, capsys):
    assert cli("undo", "--list") == 0
    assert "Nothing to undo" in capsys.readouterr().out


def test_settings_show_redacts_keys(cli, capsys):
    cli("settings", "set-key", "anthropic", "sk-secret-value")
    capsys.readouterr()
    assert cli("settings", "show") == 0
    output = capsys.readouterr().out
    assert "sk-secret-value" not in output
    assert "***set***" in output


def test_settings_set_provider_and_policy_persist(cli, tmp_path, capsys):
    assert cli("settings", "set-provider", "anthropic", "--model", "claude-sonnet-4-5") == 0
    assert cli("settings", "set-policy", "reversible", "auto") == 0
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["provider"] == "anthropic"
    assert saved["policy_reversible"] == "auto"
    assert saved["providers"]["anthropic"]["model"] == "claude-sonnet-4-5"


def test_settings_folder_whitelist_can_be_edited(cli, tmp_path, home, capsys):
    extra = tmp_path / "work"
    extra.mkdir()
    cli("settings", "allow-folder", str(extra))
    saved = json.loads((tmp_path / "config.json").read_text())
    assert str(extra) in saved["allowed_folders"]

    cli("settings", "remove-folder", str(extra))
    saved = json.loads((tmp_path / "config.json").read_text())
    assert str(extra) not in saved["allowed_folders"]


def test_settings_set_parses_option_types(cli, tmp_path, capsys):
    cli("settings", "set", "dry-run-default", "true")
    cli("settings", "set", "max-tool-iterations", "7")
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["dry_run_default"] is True
    assert saved["max_tool_iterations"] == 7


def test_settings_set_rejects_unknown_options(cli, capsys):
    assert cli("settings", "set", "not-an-option", "1") == 1
    assert "Unknown option" in capsys.readouterr().out
