"""Persistence for teacher-note drafts (`note_drafts`); a draft is only ever a stored text, nothing is sent."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from .repo import RepoError

_COLUMNS = "id, flag, status, created_at, actor"


def insert_note(conn: sqlite3.Connection, assignment_id: int, *, flag: str, body: str, actor: str,
                now: datetime) -> int:
    cur = conn.execute("INSERT INTO note_drafts (assignment_id, flag, body, status, created_at, actor)"
                       " VALUES (?, ?, ?, 'draft', ?, ?)", (assignment_id, flag, body, now.isoformat(), actor))
    if not cur.lastrowid:
        raise RepoError("note draft insert had no effect")
    return int(cur.lastrowid)


def get_note(conn: sqlite3.Connection, assignment_id: int, note_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM note_drafts WHERE id = ? AND assignment_id = ?",
                       (note_id, assignment_id)).fetchone()
    return dict(row) if row else None


def set_status(conn: sqlite3.Connection, note_id: int, status: str) -> None:
    conn.execute("UPDATE note_drafts SET status = ? WHERE id = ?", (status, note_id))


def notes_for(conn: sqlite3.Connection, assignment_id: int) -> list[dict[str, Any]]:
    """Newest first; the body is not part of the listing."""
    rows = conn.execute(f"SELECT {_COLUMNS} FROM note_drafts WHERE assignment_id = ? ORDER BY id DESC",
                        (assignment_id,)).fetchall()
    return [dict(r) for r in rows]
