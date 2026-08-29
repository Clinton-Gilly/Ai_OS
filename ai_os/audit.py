"""Local SQLite store: audit log, undo stack, and the memory table.

Everything stays on the machine. The audit log records every request, every
proposed action, the decision made about it, and the outcome — which is what
makes the approval flow reviewable after the fact.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import database_path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    text         TEXT NOT NULL,
    interface    TEXT NOT NULL DEFAULT 'cli',
    provider     TEXT,
    model        TEXT,
    dry_run      INTEGER NOT NULL DEFAULT 0,
    response     TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id    INTEGER REFERENCES requests(id),
    created_at    TEXT NOT NULL,
    action        TEXT NOT NULL,
    skill         TEXT NOT NULL,
    arguments     TEXT NOT NULL,
    summary       TEXT NOT NULL,
    risk          TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    decision_note TEXT,
    outcome       TEXT NOT NULL,
    message       TEXT,
    dry_run       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS undo_stack (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id   INTEGER REFERENCES actions(id),
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    description TEXT NOT NULL,
    undone_at   TEXT
);

CREATE TABLE IF NOT EXISTS memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind       TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    UNIQUE(kind, key)
);

CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_actions_request ON actions(request_id);
CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at);
CREATE INDEX IF NOT EXISTS idx_undo_pending ON undo_stack(undone_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class UndoEntry:
    id: int
    action_id: int | None
    created_at: str
    kind: str
    payload: dict[str, Any]
    description: str


class AuditLog:
    """Thin, synchronous wrapper around the SQLite database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT COUNT(*) FROM schema_info")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- requests -------------------------------------------------------
    def start_request(self, text: str, *, interface: str = "cli", provider: str = "",
                      model: str = "", dry_run: bool = False) -> int:
        cur = self._conn.execute(
            "INSERT INTO requests(created_at, text, interface, provider, model, dry_run)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), text, interface, provider, model, int(dry_run)),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_request(self, request_id: int, response: str) -> None:
        self._conn.execute(
            "UPDATE requests SET response = ? WHERE id = ?", (response, request_id)
        )
        self._conn.commit()

    # -- actions --------------------------------------------------------
    def record_action(self, *, request_id: int | None, action: str, skill: str,
                      arguments: dict[str, Any], summary: str, risk: str, verdict: str,
                      decision_note: str, outcome: str, message: str,
                      dry_run: bool) -> int:
        cur = self._conn.execute(
            "INSERT INTO actions(request_id, created_at, action, skill, arguments, summary,"
            " risk, verdict, decision_note, outcome, message, dry_run)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_id, _now(), action, skill, json.dumps(arguments, default=str),
                summary, risk, verdict, decision_note, outcome, message, int(dry_run),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def recent_actions(self, limit: int = 20, search: str = "") -> list[sqlite3.Row]:
        if search:
            pattern = f"%{search}%"
            return list(self._conn.execute(
                "SELECT * FROM actions WHERE action LIKE ? OR summary LIKE ? OR message LIKE ?"
                " ORDER BY id DESC LIMIT ?",
                (pattern, pattern, pattern, limit),
            ))
        return list(self._conn.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)
        ))

    def recent_requests(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)
        ))

    # -- undo -----------------------------------------------------------
    def push_undo(self, action_id: int | None, kind: str, payload: dict[str, Any],
                  description: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO undo_stack(action_id, created_at, kind, payload, description)"
            " VALUES (?, ?, ?, ?, ?)",
            (action_id, _now(), kind, json.dumps(payload, default=str), description),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def pending_undo(self, limit: int = 20) -> list[UndoEntry]:
        rows = self._conn.execute(
            "SELECT * FROM undo_stack WHERE undone_at IS NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [self._to_undo_entry(row) for row in rows]

    def get_undo(self, undo_id: int) -> UndoEntry | None:
        row = self._conn.execute(
            "SELECT * FROM undo_stack WHERE id = ? AND undone_at IS NULL", (undo_id,)
        ).fetchone()
        return self._to_undo_entry(row) if row else None

    def mark_undone(self, undo_id: int) -> None:
        self._conn.execute(
            "UPDATE undo_stack SET undone_at = ? WHERE id = ?", (_now(), undo_id)
        )
        self._conn.commit()

    @staticmethod
    def _to_undo_entry(row: sqlite3.Row) -> UndoEntry:
        return UndoEntry(
            id=row["id"],
            action_id=row["action_id"],
            created_at=row["created_at"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            description=row["description"],
        )

    # -- memory (schema laid down now, used by the Phase 2 memory system) --
    def remember(self, kind: str, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT INTO memory(created_at, kind, key, value) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(kind, key) DO UPDATE SET value = excluded.value,"
            " created_at = excluded.created_at",
            (_now(), kind, key, json.dumps(value, default=str)),
        )
        self._conn.commit()

    def recall(self, kind: str, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT value FROM memory WHERE kind = ? AND key = ?", (kind, key)
        ).fetchone()
        return json.loads(row["value"]) if row else None

    def memories(self, kind: str = "") -> list[sqlite3.Row]:
        if kind:
            return list(self._conn.execute(
                "SELECT * FROM memory WHERE kind = ? ORDER BY id DESC", (kind,)
            ))
        return list(self._conn.execute("SELECT * FROM memory ORDER BY id DESC"))

    def forget(self, kind: str = "", key: str = "") -> int:
        if kind and key:
            cur = self._conn.execute(
                "DELETE FROM memory WHERE kind = ? AND key = ?", (kind, key))
        elif kind:
            cur = self._conn.execute("DELETE FROM memory WHERE kind = ?", (kind,))
        else:
            cur = self._conn.execute("DELETE FROM memory")
        self._conn.commit()
        return cur.rowcount


def format_rows(rows: Iterable[sqlite3.Row], columns: list[str]) -> list[dict[str, Any]]:
    return [{column: row[column] for column in columns} for row in rows]
