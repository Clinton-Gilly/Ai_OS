"""The tool router: skills register here, and actions are dispatched by name."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from .base import ActionDef, ActionResult, ExecContext, Skill, UndoRecord

UndoHandler = Callable[[UndoRecord, ExecContext], ActionResult]


class SkillRegistry:
    """Holds every registered skill and resolves action names to handlers."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._actions: dict[str, ActionDef] = {}
        self._undo: dict[str, UndoHandler] = {}

    def register(self, skill: Skill) -> None:
        if not skill.name:
            raise ValueError("skill is missing a name")
        if skill.name in self._skills:
            raise ValueError(f"skill {skill.name!r} is already registered")
        actions = list(skill.actions())
        for action in actions:
            if action.name in self._actions:
                raise ValueError(f"action {action.name!r} is already registered")
        self._skills[skill.name] = skill
        for action in actions:
            self._actions[action.name] = action
        self._undo.update(skill.undo_handlers())

    def register_all(self, skills: Iterable[Skill]) -> None:
        for skill in skills:
            self.register(skill)

    # -- lookups --------------------------------------------------------
    def skills(self) -> list[Skill]:
        return list(self._skills.values())

    def actions(self) -> list[ActionDef]:
        return list(self._actions.values())

    def get(self, name: str) -> ActionDef | None:
        return self._actions.get(name)

    def require(self, name: str) -> ActionDef:
        action = self._actions.get(name)
        if action is None:
            raise KeyError(f"unknown action {name!r}")
        return action

    def undo_handler(self, kind: str) -> UndoHandler | None:
        return self._undo.get(kind)

    def __iter__(self) -> Iterator[ActionDef]:
        return iter(self._actions.values())

    def __len__(self) -> int:
        return len(self._actions)

    def tool_schemas(self) -> list[dict]:
        """Provider-neutral tool definitions handed to the LLM router."""
        return [
            {
                "name": action.name,
                "description": (
                    f"{action.description} (risk: {action.risk.label})"
                ),
                "parameters": action.json_schema(),
            }
            for action in sorted(self._actions.values(), key=lambda a: a.name)
        ]


def default_registry(memory: Any = None) -> SkillRegistry:
    """Registry with the standard skill set loaded.

    ``memory`` is a MemoryStore. Pass one to expose the memory skill; without
    it every other skill still works, so a registry can be built with no
    database behind it.
    """
    from .apps import AppSkill
    from .browser import BrowserSkill
    from .diagnostics import DiagnosticsSkill
    from .filesystem import FileSystemSkill
    from .power import PowerSkill
    from .scheduler import SchedulerSkill
    from .system import SystemSkill

    registry = SkillRegistry()
    registry.register_all([
        FileSystemSkill(),
        AppSkill(),
        BrowserSkill(),
        PowerSkill(),
        SystemSkill(),
        DiagnosticsSkill(),
        SchedulerSkill(),
    ])
    if memory is not None:
        from .memory_skill import MemorySkill

        registry.register(MemorySkill(memory))
    return registry
