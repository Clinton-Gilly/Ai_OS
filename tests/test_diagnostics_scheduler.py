"""Diagnostics report on the machine; the scheduler talks to Task Scheduler."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from ai_os.skills.base import ExecContext, RiskLevel
from ai_os.skills.diagnostics import DiagnosticsSkill
from ai_os.skills.scheduler import SchedulerSkill, parse_when, task_name


def ctx(config=None, dry_run=False):
    return ExecContext(dry_run=dry_run, config=config)


# -- diagnostics --------------------------------------------------------
def test_every_diagnostic_action_is_read_only():
    for action in DiagnosticsSkill().actions():
        assert action.risk is RiskLevel.READ_ONLY


def test_report_always_produces_findings(config):
    result = DiagnosticsSkill().report({}, ctx(config))
    assert result.ok
    assert result.data["findings"]
    assert result.data["process_count"] >= 1
    assert result.data["disks"]


def test_report_flags_a_nearly_full_disk(config, monkeypatch):
    import shutil

    class FakeUsage:
        total = 500 * 1024 ** 3
        used = 495 * 1024 ** 3
        free = 5 * 1024 ** 3

    monkeypatch.setattr(shutil, "disk_usage", lambda _path: FakeUsage)
    result = DiagnosticsSkill().report({}, ctx(config))
    assert any("nearly full" in finding for finding in result.data["findings"])
    assert any("Free space" in s for s in result.data["suggestions"])


def test_report_flags_a_memory_hungry_process(config, monkeypatch):
    from ai_os.platform_win import ProcessInfo

    monkeypatch.setattr("ai_os.skills.diagnostics.list_processes",
                        lambda: [ProcessInfo("chrome.exe", 42, 4_000_000)])
    result = DiagnosticsSkill().report({}, ctx(config))
    assert any("Memory-hungry" in finding for finding in result.data["findings"])


def test_report_flags_a_long_uptime(config, monkeypatch):
    monkeypatch.setattr("ai_os.skills.diagnostics._uptime_seconds",
                        lambda: 30 * 86400)
    result = DiagnosticsSkill().report({}, ctx(config))
    assert any("up for 30 days" in finding for finding in result.data["findings"])


def test_top_processes_is_sorted_and_limited(config):
    result = DiagnosticsSkill().top_processes({"limit": 3}, ctx(config))
    rows = result.data["processes"]
    assert len(rows) <= 3
    assert rows == sorted(rows, key=lambda row: row["memory_mb"], reverse=True)


def test_large_files_finds_what_is_actually_large(home, config):
    small = home / "small.bin"
    small.write_bytes(b"0" * 1024)
    big = home / "big.bin"
    big.write_bytes(b"0" * (2 * 1024 * 1024))

    result = DiagnosticsSkill().large_files(
        {"path": str(home), "min_mb": 1}, ctx(config))
    paths = [row["path"] for row in result.data["files"]]
    assert str(big) in paths
    assert str(small) not in paths


def test_large_files_reports_nothing_found_clearly(home, config):
    result = DiagnosticsSkill().large_files(
        {"path": str(home), "min_mb": 500}, ctx(config))
    assert result.ok and result.data["files"] == []
    assert "No files over" in result.message


def test_large_files_rejects_a_missing_folder(home, config):
    result = DiagnosticsSkill().large_files(
        {"path": str(home / "nope")}, ctx(config))
    assert not result.ok


# -- scheduler ----------------------------------------------------------
def test_task_names_are_namespaced_and_sanitized():
    assert task_name("morning routine") == "AiOS_morning_routine"
    assert task_name("bad/name*here").startswith("AiOS_")
    assert task_name("AiOS_already") == "AiOS_already"
    assert task_name("!!!") == "AiOS_task"


@pytest.mark.parametrize("text", ["17:30", "at 17:30", "5pm", "9am", "in 20 minutes",
                                  "in 2 hours"])
def test_when_expressions_parse(text):
    date, time = parse_when(text)
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", date)
    assert re.fullmatch(r"\d{2}:\d{2}", time)


def test_a_time_already_past_moves_to_tomorrow():
    now = datetime.now()
    past = (now - timedelta(hours=2)).strftime("%H:%M")
    date, _ = parse_when(past)
    tomorrow = (now + timedelta(days=1)).strftime("%m/%d/%Y")
    assert date == tomorrow


def test_unreadable_times_are_refused():
    with pytest.raises(ValueError):
        parse_when("sometime soon")


def test_scheduling_a_command_is_destructive_but_a_reminder_is_not():
    risks = {action.name: action.risk for action in SchedulerSkill().actions()}
    assert risks["schedule_reminder"] is RiskLevel.REVERSIBLE
    assert risks["schedule_command"] is RiskLevel.DESTRUCTIVE
    assert risks["schedule_delete"] is RiskLevel.DESTRUCTIVE
    assert risks["schedule_list"] is RiskLevel.READ_ONLY


def test_reminder_dry_run_describes_without_scheduling(config):
    result = SchedulerSkill().create_reminder(
        {"name": "stretch", "message": "stand up", "when": "in 30 minutes"},
        ctx(config, dry_run=True))
    assert result.ok and result.data["dry_run"]
    assert result.data["task"] == "AiOS_stretch"


def test_a_bad_time_is_reported_before_anything_is_scheduled(config):
    result = SchedulerSkill().create_reminder(
        {"name": "x", "message": "y", "when": "whenever"}, ctx(config))
    assert not result.ok and "Could not read" in result.message


def test_a_bad_weekday_is_refused(config):
    result = SchedulerSkill().create_reminder(
        {"name": "x", "message": "y", "when": "9am", "repeat": "weekly",
         "day": "FUNDAY"}, ctx(config, dry_run=True))
    assert not result.ok and "weekday" in result.message


def test_scheduling_off_windows_says_so_rather_than_failing_oddly(config, monkeypatch):
    monkeypatch.setattr("ai_os.skills.scheduler.is_windows", lambda: False)
    result = SchedulerSkill().create_reminder(
        {"name": "x", "message": "y", "when": "9am"}, ctx(config))
    assert not result.ok and "only available on Windows" in result.message


def test_reminder_command_does_not_break_on_quotes(config):
    from ai_os.skills.scheduler import _powershell_message

    command = _powershell_message("it's time")
    assert "it''s time" in command


def test_scheduler_uses_the_expected_schtasks_arguments(config, monkeypatch):
    captured: dict = {}

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeResult()

    monkeypatch.setattr("ai_os.skills.scheduler.is_windows", lambda: True)
    monkeypatch.setattr("ai_os.skills.scheduler.run", fake_run)
    result = SchedulerSkill().create_reminder(
        {"name": "stretch", "message": "stand up", "when": "17:30",
         "repeat": "daily"}, ctx(config))
    argv = captured["argv"]
    assert argv[:3] == ["schtasks", "/create", "/tn"]
    assert "AiOS_stretch" in argv
    assert "/sc" in argv and "DAILY" in argv
    assert result.ok and result.undo is not None
