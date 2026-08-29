"""The audit log, undo stack, memory table, and settings persistence."""

from __future__ import annotations

import json
import os

from ai_os.audit import AuditLog
from ai_os.config import Config, ProviderConfig, load_config, save_config


def make_log(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "audit.sqlite3")


def test_requests_and_actions_are_linked(tmp_path):
    log = make_log(tmp_path)
    request_id = log.start_request("delete a.txt", provider="anthropic", model="claude")
    log.record_action(request_id=request_id, action="fs_delete", skill="filesystem",
                      arguments={"path": "a.txt"}, summary="Delete a.txt",
                      risk="destructive", verdict="approve", decision_note="policy",
                      outcome="executed", message="Deleted.", dry_run=False)
    log.finish_request(request_id, "Deleted a.txt.")

    action = log.recent_actions(1)[0]
    assert action["request_id"] == request_id
    assert json.loads(action["arguments"]) == {"path": "a.txt"}
    assert log.recent_requests(1)[0]["response"] == "Deleted a.txt."
    log.close()


def test_audit_search_matches_summary_and_message(tmp_path):
    log = make_log(tmp_path)
    for name in ("holiday.png", "invoice.pdf"):
        log.record_action(request_id=None, action="fs_move", skill="filesystem",
                          arguments={}, summary=f"Move {name}", risk="reversible",
                          verdict="approve", decision_note="", outcome="executed",
                          message=f"Moved {name}.", dry_run=False)
    assert len(log.recent_actions(10, "invoice")) == 1
    assert len(log.recent_actions(10)) == 2
    log.close()


def test_undo_stack_is_newest_first_and_marks_entries_done(tmp_path):
    log = make_log(tmp_path)
    first = log.push_undo(None, "fs.move", {"from": "a", "to": "b"}, "move a")
    second = log.push_undo(None, "fs.move", {"from": "c", "to": "d"}, "move c")
    assert [entry.id for entry in log.pending_undo()] == [second, first]

    log.mark_undone(second)
    assert [entry.id for entry in log.pending_undo()] == [first]
    assert log.get_undo(second) is None
    log.close()


def test_memory_upserts_and_can_be_forgotten(tmp_path):
    log = make_log(tmp_path)
    log.remember("folder", "desktop", "C:/Users/x/Desktop")
    log.remember("folder", "desktop", "D:/Desktop")
    assert log.recall("folder", "desktop") == "D:/Desktop"
    assert len(log.memories("folder")) == 1
    assert log.forget("folder", "desktop") == 1
    assert log.recall("folder", "desktop") is None
    log.close()


def test_reopening_the_database_keeps_history(tmp_path):
    log = make_log(tmp_path)
    log.start_request("hello")
    log.close()
    reopened = make_log(tmp_path)
    assert len(reopened.recent_requests(5)) == 1
    reopened.close()


def test_config_round_trips_through_disk(tmp_path):
    path = tmp_path / "config.json"
    config = Config(provider="anthropic", allowed_folders=["C:/Users/x/Desktop"])
    config.provider_config("anthropic").api_key = "sk-secret"
    config.provider_config("anthropic").model = "claude-sonnet-4-5"
    save_config(config, path)

    loaded = load_config(path)
    assert loaded.provider == "anthropic"
    assert loaded.allowed_folders == ["C:/Users/x/Desktop"]
    assert loaded.provider_config("anthropic").api_key == "sk-secret"


def test_saved_config_is_not_world_readable(tmp_path):
    path = tmp_path / "config.json"
    save_config(Config(), path)
    if os.name != "nt":
        assert oct(path.stat().st_mode)[-3:] == "600"


def test_redacted_config_hides_api_keys():
    config = Config(providers={"anthropic": ProviderConfig(api_key="sk-secret")})
    dumped = json.dumps(config.redacted())
    assert "sk-secret" not in dumped
    assert "***set***" in dumped


def test_unknown_or_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_config(path).provider == Config().provider

    path.write_text(json.dumps({"provider": "openai", "made_up_key": 1}), encoding="utf-8")
    assert load_config(path).provider == "openai"


def test_allowed_folders_default_to_the_home_directory(home):
    assert Config().effective_allowed_folders() == [home]
