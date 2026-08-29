"""Skill registry and file-operation behaviour."""

from __future__ import annotations

import pytest

from ai_os.skills import default_registry
from ai_os.skills.base import ActionDef, ActionResult, ExecContext, Param, RiskLevel, Skill
from ai_os.skills.filesystem import FileSystemSkill, category_for
from ai_os.skills.registry import SkillRegistry


def ctx(config=None, dry_run=False):
    return ExecContext(dry_run=dry_run, config=config)


def test_registry_exposes_every_action_uniquely():
    registry = default_registry()
    names = [action.name for action in registry]
    assert len(names) == len(set(names))
    assert {"fs_delete", "app_open", "browser_open", "power_shutdown"} <= set(names)


def test_tool_schema_shape():
    registry = default_registry()
    schema = {tool["name"]: tool for tool in registry.tool_schemas()}
    move = schema["fs_move"]
    assert move["parameters"]["required"] == ["source", "destination"]
    assert "risk" in move["description"]


def test_registering_a_duplicate_skill_fails():
    registry = SkillRegistry()
    registry.register(FileSystemSkill())
    with pytest.raises(ValueError):
        registry.register(FileSystemSkill())


def test_a_new_skill_needs_no_core_changes():
    """The plugin promise: registering a skill is all it takes to expose it."""

    class GreetSkill(Skill):
        name = "greet"
        description = "Say hello."

        def actions(self):
            return (ActionDef(
                name="greet_hello",
                skill=self.name,
                description="Say hello to someone.",
                risk=RiskLevel.READ_ONLY,
                handler=lambda args, _ctx: ActionResult(True, f"Hello, {args['who']}!"),
                params=(Param("who", description="Who to greet."),),
            ),)

    registry = default_registry()
    registry.register(GreetSkill())
    action = registry.require("greet_hello")
    assert action.handler({"who": "Clinton"}, ctx()).message == "Hello, Clinton!"


def test_validate_rejects_unknown_and_missing_arguments():
    action = default_registry().require("fs_rename")
    with pytest.raises(ValueError):
        action.validate({"path": "/tmp/x"})
    with pytest.raises(ValueError):
        action.validate({"path": "/tmp/x", "new_name": "y", "colour": "red"})


def test_validate_coerces_loose_json_types():
    action = default_registry().require("fs_find")
    cleaned = action.validate({"path": "/tmp", "pattern": "*.log", "max_results": "5"})
    assert cleaned["max_results"] == 5


def test_category_for_known_and_unknown_extensions(tmp_path):
    assert category_for(tmp_path / "a.PNG") == "Images"
    assert category_for(tmp_path / "a.pdf") == "Documents"
    assert category_for(tmp_path / "a.zzz") == "Other"


def test_organize_dry_run_changes_nothing(home, config):
    skill = FileSystemSkill()
    (home / "a.png").write_text("x")
    (home / "b.pdf").write_text("x")
    result = skill.organize({"path": str(home), "by": "type"}, ctx(config, dry_run=True))
    assert result.ok and result.data["dry_run"]
    assert len(result.data["moves"]) == 2
    assert (home / "a.png").exists()
    assert not (home / "Images").exists()


def test_organize_then_undo_restores_the_folder(home, config):
    skill = FileSystemSkill()
    for name in ("a.png", "b.pdf", "c.txt"):
        (home / name).write_text("x")
    result = skill.organize({"path": str(home), "by": "type"}, ctx(config))
    assert result.ok
    assert (home / "Images" / "a.png").exists()
    assert result.undo is not None

    undo = skill.undo_handlers()["fs.organize"](result.undo, ctx(config))
    assert undo.ok
    assert (home / "a.png").exists()
    assert not (home / "Images").exists()


def test_move_never_overwrites(home, config):
    skill = FileSystemSkill()
    (home / "docs").mkdir()
    (home / "docs" / "note.txt").write_text("existing")
    (home / "note.txt").write_text("new")
    result = skill.move({"source": str(home / "note.txt"),
                         "destination": str(home / "docs")}, ctx(config))
    assert result.ok
    assert (home / "docs" / "note.txt").read_text() == "existing"
    assert (home / "docs" / "note (2).txt").read_text() == "new"


def test_rename_and_undo(home, config):
    skill = FileSystemSkill()
    (home / "old.txt").write_text("x")
    result = skill.rename({"path": str(home / "old.txt"), "new_name": "new.txt"}, ctx(config))
    assert result.ok and (home / "new.txt").exists()
    skill.undo_handlers()["fs.rename"](result.undo, ctx(config))
    assert (home / "old.txt").exists()


def test_rename_rejects_a_path_as_the_new_name(home, config):
    skill = FileSystemSkill()
    (home / "old.txt").write_text("x")
    result = skill.rename({"path": str(home / "old.txt"), "new_name": "sub/new.txt"},
                          ctx(config))
    assert not result.ok


def test_delete_is_recoverable_and_undoable(home, config):
    skill = FileSystemSkill()
    target = home / "junk.txt"
    target.write_text("bye")
    result = skill.delete({"path": str(target)}, ctx(config))
    assert result.ok and not target.exists()
    assert result.undo is not None
    undo = skill.undo_handlers()["fs.delete"](result.undo, ctx(config))
    assert undo.ok and target.read_text() == "bye"
