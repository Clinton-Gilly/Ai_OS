"""Memory as a skill, so the assistant can be told what to remember.

The store is injected rather than constructed here: the same MemoryStore backs
the skill, the Settings page, and the `ai-os memory` commands, so what the
model remembers is exactly what you can see and delete.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..memory import KINDS, MemoryStore
from .base import (
    ActionDef,
    ActionResult,
    ExecContext,
    Param,
    RiskLevel,
    Skill,
    UndoRecord,
)


class MemorySkill(Skill):
    name = "memory"
    description = "Remember, recall, and forget things about how you work."

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def actions(self) -> Sequence[ActionDef]:
        return (
            ActionDef(
                name="memory_list",
                skill=self.name,
                description="List what is remembered, optionally filtered by kind.",
                risk=RiskLevel.READ_ONLY,
                handler=self.list_items,
                params=(
                    Param("kind", required=False, default="", enum=KINDS,
                          description="Only list this kind of memory."),
                ),
                summarize=lambda a: f"List remembered {a.get('kind') or 'items'}",
            ),
            ActionDef(
                name="memory_remember",
                skill=self.name,
                description=(
                    "Remember something durable: a preference, a fact, or a "
                    "folder the user works in."
                ),
                risk=RiskLevel.REVERSIBLE,
                handler=self.remember,
                reversible=True,
                params=(
                    Param("kind", enum=KINDS, description="What sort of memory this is."),
                    Param("key", description="Short name for the thing remembered."),
                    Param("value", description="What to remember about it."),
                ),
                summarize=lambda a: f"Remember {a['kind']}: {a['key']} = {a['value']}",
            ),
            ActionDef(
                name="memory_forget",
                skill=self.name,
                description="Forget one remembered item, or a whole kind of memory.",
                risk=RiskLevel.REVERSIBLE,
                handler=self.forget,
                reversible=True,
                params=(
                    Param("kind", required=False, default="", enum=KINDS,
                          description="Kind to forget. Omit to forget everything."),
                    Param("key", required=False, default="",
                          description="Single item to forget. Omit to forget the kind."),
                ),
                summarize=lambda a: (
                    f"Forget {a.get('kind') or 'everything'}"
                    + (f": {a['key']}" if a.get("key") else "")
                ),
            ),
        )

    def undo_handlers(self) -> dict[str, Callable[[UndoRecord, ExecContext], ActionResult]]:
        return {
            "memory.remember": self._undo_remember,
            "memory.forget": self._undo_forget,
        }

    def list_items(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        items = self.store.items(args.get("kind") or "")
        rows = [
            {"kind": item.kind, "key": item.key, "value": item.value,
             "uses": item.uses}
            for item in items
        ]
        if not rows:
            return ActionResult(True, "Nothing is remembered yet.", {"items": []})
        return ActionResult(
            True,
            f"{len(rows)} remembered item(s): "
            + "; ".join(item.describe() for item in items[:10]),
            {"items": rows},
        )

    def remember(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        kind, key, value = args["kind"], args["key"], args["value"]
        if ctx.dry_run:
            return ActionResult(True, f"Would remember {kind}: {key} = {value}.",
                                {"dry_run": True})
        try:
            previous = self.store.recall(kind, key)
            self.store.remember(kind, key, value)
        except ValueError as exc:
            return ActionResult(False, str(exc))
        return ActionResult(
            True,
            f"Remembered {kind}: {key} = {value}.",
            {"kind": kind, "key": key},
            UndoRecord("memory.remember", {"kind": kind, "key": key, "previous": previous},
                       f"Forget {kind}: {key}"),
        )

    def forget(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        kind = args.get("kind") or ""
        key = args.get("key") or ""
        target = f"{kind}: {key}" if key else (kind or "everything")
        if ctx.dry_run:
            count = len(self.store.search(key) if key else self.store.items(kind))
            return ActionResult(True, f"Would forget {target} ({count} item(s)).",
                                {"dry_run": True})
        # Snapshot first so the removal can be undone.
        removed = [
            {"kind": item.kind, "key": item.key, "value": item.value}
            for item in self.store.items(kind)
            if not key or item.key == key
        ]
        try:
            count = self.store.forget(kind, key)
        except ValueError as exc:
            return ActionResult(False, str(exc))
        return ActionResult(
            True,
            f"Forgot {target} ({count} item(s)).",
            {"forgotten": count},
            UndoRecord("memory.forget", {"items": removed}, f"Restore {target}"),
        )

    def _undo_remember(self, record: UndoRecord, ctx: ExecContext) -> ActionResult:
        kind, key = record.payload["kind"], record.payload["key"]
        previous = record.payload.get("previous")
        if previous is None:
            self.store.forget(kind, key)
            return ActionResult(True, f"Forgot {kind}: {key} again.")
        self.store.remember(kind, key, previous)
        return ActionResult(True, f"Restored the previous value of {kind}: {key}.")

    def _undo_forget(self, record: UndoRecord, ctx: ExecContext) -> ActionResult:
        restored = 0
        for item in record.payload.get("items", []):
            self.store.remember(item["kind"], item["key"], item["value"])
            restored += 1
        return ActionResult(True, f"Restored {restored} remembered item(s).")
