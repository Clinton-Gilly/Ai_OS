"""Undo: reverse a previously executed action using its recorded undo entry."""

from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditLog, UndoEntry
from .config import Config
from .skills.base import ActionResult, ExecContext, UndoRecord
from .skills.registry import SkillRegistry


@dataclass
class UndoOutcome:
    entry: UndoEntry
    result: ActionResult


class UndoManager:
    """Walks the undo stack, newest first."""

    def __init__(self, config: Config, registry: SkillRegistry, audit: AuditLog) -> None:
        self.config = config
        self.registry = registry
        self.audit = audit

    def pending(self, limit: int = 20) -> list[UndoEntry]:
        return self.audit.pending_undo(limit)

    def undo(self, undo_id: int | None = None) -> UndoOutcome | None:
        """Undo one entry — the newest by default. None if there is nothing to undo."""
        if undo_id is None:
            entries = self.audit.pending_undo(1)
            if not entries:
                return None
            entry = entries[0]
        else:
            entry = self.audit.get_undo(undo_id)
            if entry is None:
                return None

        handler = self.registry.undo_handler(entry.kind)
        if handler is None:
            return UndoOutcome(entry, ActionResult(
                False, f"No skill knows how to undo {entry.kind!r}."))

        record = UndoRecord(entry.kind, entry.payload, entry.description)
        ctx = ExecContext(dry_run=False, config=self.config)
        try:
            result = handler(record, ctx)
        except Exception as exc:
            result = ActionResult(False, f"Undo failed: {exc}")
        if result.ok:
            self.audit.mark_undone(entry.id)
        return UndoOutcome(entry, result)
