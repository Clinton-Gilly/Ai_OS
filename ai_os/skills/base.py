"""The skill/plugin contract.

Every capability in AI OS is a Skill exposing one or more ActionDefs. An
ActionDef carries its own risk level, parameter schema, handler, and (where it
applies) the information needed to undo it. Nothing about a specific command is
hardcoded in the router — it looks actions up in the registry by name.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class RiskLevel(IntEnum):
    """The three permission tiers from the plan."""

    READ_ONLY = 0     # auto-run: observes, changes nothing
    REVERSIBLE = 1    # cancelable and undoable (move, rename, organize)
    DESTRUCTIVE = 2   # must be approved: delete, power, anything one-way

    @property
    def label(self) -> str:
        return {
            RiskLevel.READ_ONLY: "read-only",
            RiskLevel.REVERSIBLE: "reversible",
            RiskLevel.DESTRUCTIVE: "destructive",
        }[self]

    @classmethod
    def parse(cls, value: str | int | RiskLevel) -> RiskLevel:
        if isinstance(value, RiskLevel):
            return value
        if isinstance(value, int):
            return cls(value)
        key = str(value).strip().lower().replace("-", "_")
        aliases = {
            "read_only": cls.READ_ONLY,
            "readonly": cls.READ_ONLY,
            "auto": cls.READ_ONLY,
            "reversible": cls.REVERSIBLE,
            "destructive": cls.DESTRUCTIVE,
        }
        if key not in aliases:
            raise ValueError(f"unknown risk level: {value!r}")
        return aliases[key]


@dataclass(frozen=True)
class Param:
    """One action parameter, used for both validation and the LLM tool schema."""

    name: str
    type: str = "string"           # string | integer | number | boolean | array
    description: str = ""
    required: bool = True
    default: Any = None
    enum: tuple[str, ...] | None = None
    is_path: bool = False          # checked against the allowed-folder whitelist

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.type == "array":
            schema["items"] = {"type": "string"}
        if self.enum:
            schema["enum"] = list(self.enum)
        return schema


@dataclass
class UndoRecord:
    """How to reverse an action that has already run."""

    kind: str                       # dispatch key, e.g. "fs.move"
    payload: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class ActionResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    undo: UndoRecord | None = None


@dataclass
class ExecContext:
    """Everything a handler is allowed to know about the run."""

    dry_run: bool = False
    config: Any = None              # ai_os.config.Config (untyped to avoid a cycle)


Handler = Callable[[dict[str, Any], ExecContext], ActionResult]
Summarizer = Callable[[dict[str, Any]], str]


@dataclass
class ActionDef:
    """A single callable capability."""

    name: str                       # unique tool name, e.g. "fs_move"
    skill: str
    description: str
    risk: RiskLevel
    handler: Handler
    params: tuple[Param, ...] = ()
    reversible: bool = False
    summarize: Summarizer | None = None
    # Actions that can touch many items at once always get a dry-run preview.
    bulk: bool = False

    def param(self, name: str) -> Param | None:
        for param in self.params:
            if param.name == name:
                return param
        return None

    def path_params(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.is_path)

    def describe(self, args: dict[str, Any]) -> str:
        if self.summarize:
            return self.summarize(args)
        rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"{self.name}({rendered})"

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check required params, drop unknown ones, and apply defaults."""
        known = {p.name for p in self.params}
        unknown = set(args) - known
        if unknown:
            raise ValueError(
                f"{self.name}: unexpected argument(s): {', '.join(sorted(unknown))}"
            )
        cleaned: dict[str, Any] = {}
        for param in self.params:
            if param.name in args and args[param.name] is not None:
                cleaned[param.name] = _coerce(param, args[param.name])
            elif param.required:
                raise ValueError(f"{self.name}: missing required argument {param.name!r}")
            elif param.default is not None:
                cleaned[param.name] = param.default
        return cleaned

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {p.name: p.json_schema() for p in self.params},
            "required": [p.name for p in self.params if p.required],
        }


def _coerce(param: Param, value: Any) -> Any:
    """Models send loosely typed JSON; normalize it before a handler sees it."""
    if param.type == "boolean" and isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "on"}
    if param.type == "integer" and isinstance(value, str):
        return int(value.strip())
    if param.type == "number" and isinstance(value, str):
        return float(value.strip())
    if param.type == "array" and isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if param.enum and isinstance(value, str):
        lowered = value.strip().lower()
        for option in param.enum:
            if option.lower() == lowered:
                return option
    return value


class Skill:
    """Base class for a pluggable capability module."""

    name: str = ""
    description: str = ""

    def actions(self) -> Sequence[ActionDef]:  # pragma: no cover - interface
        raise NotImplementedError

    def undo_handlers(self) -> dict[str, Callable[[UndoRecord, ExecContext], ActionResult]]:
        """Map UndoRecord.kind to the function that reverses it."""
        return {}
