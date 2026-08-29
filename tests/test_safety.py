"""The permission engine decides what runs, what asks, and what is refused."""

from __future__ import annotations

import os
from pathlib import Path

from ai_os.config import POLICY_AUTO, POLICY_CONFIRM, POLICY_DENY, Config
from ai_os.safety import SafetyEngine, Verdict, is_protected_path, within_allowed
from ai_os.skills import default_registry


def engine(config):
    return SafetyEngine(config)


def test_read_only_actions_run_automatically(config):
    registry = default_registry()
    decision = engine(config).evaluate(registry.require("system_time"), {})
    assert decision.verdict is Verdict.ALLOW


def test_reversible_actions_ask_first(home, config):
    registry = default_registry()
    decision = engine(config).evaluate(
        registry.require("fs_rename"), {"path": str(home / "a.txt"), "new_name": "b.txt"})
    assert decision.verdict is Verdict.APPROVE


def test_destructive_actions_always_ask_even_on_auto(home, config):
    config.policy_destructive = POLICY_AUTO
    registry = default_registry()
    decision = engine(config).evaluate(
        registry.require("fs_delete"), {"path": str(home / "a.txt")})
    assert decision.verdict is Verdict.APPROVE
    assert any("always require approval" in reason for reason in decision.reasons)


def test_a_tier_set_to_deny_is_refused(home, config):
    config.policy_reversible = POLICY_DENY
    registry = default_registry()
    decision = engine(config).evaluate(
        registry.require("fs_create_folder"), {"path": str(home / "new")})
    assert decision.verdict is Verdict.DENY


def test_paths_outside_the_whitelist_are_refused(tmp_path, home, config):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    registry = default_registry()
    decision = engine(config).evaluate(
        registry.require("fs_delete"), {"path": str(outside / "x.txt")})
    assert decision.verdict is Verdict.DENY
    assert "outside the allowed folders" in decision.explain()


def test_warn_mode_downgrades_a_whitelist_miss_to_approval(tmp_path, home, config):
    config.whitelist_mode = "warn"
    config.policy_read_only = POLICY_AUTO
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    registry = default_registry()
    decision = engine(config).evaluate(registry.require("fs_list"), {"path": str(outside)})
    assert decision.verdict is Verdict.APPROVE


def test_protected_system_locations_are_never_allowed(config):
    config.whitelist_mode = "warn"
    config.allowed_folders = ["C:/" if os.name == "nt" else "/"]
    target = "C:/Windows/System32/drivers" if os.name == "nt" else "/etc/hosts"
    assert is_protected_path(Path(target))
    registry = default_registry()
    decision = engine(config).evaluate(registry.require("fs_delete"), {"path": target})
    assert decision.verdict is Verdict.DENY


def test_bulk_actions_are_previewed_first(home, config):
    registry = default_registry()
    decision = engine(config).evaluate(registry.require("fs_organize"), {"path": str(home)})
    assert decision.force_dry_run


def test_within_allowed_handles_nested_paths(home):
    nested = home / "projects" / "app"
    nested.mkdir(parents=True)
    assert within_allowed(nested, [home])
    assert not within_allowed(home.parent, [home])


def test_default_policies_match_the_plan():
    defaults = Config()
    assert defaults.policy_read_only == POLICY_AUTO
    assert defaults.policy_reversible == POLICY_CONFIRM
    assert defaults.policy_destructive == POLICY_CONFIRM
