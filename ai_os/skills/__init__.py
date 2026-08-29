"""Pluggable capability modules.

Adding a capability means adding a Skill here and registering it — no changes
to the router, the permission engine, or the CLI.
"""

from .base import (
    ActionDef,
    ActionResult,
    ExecContext,
    Param,
    RiskLevel,
    Skill,
    UndoRecord,
)
from .registry import SkillRegistry, default_registry

__all__ = [
    "ActionDef",
    "ActionResult",
    "ExecContext",
    "Param",
    "RiskLevel",
    "Skill",
    "SkillRegistry",
    "UndoRecord",
    "default_registry",
]
