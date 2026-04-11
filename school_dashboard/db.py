import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Create items table and indexes if they don't exist."""
    conn = _connect(db_path)
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                child        TEXT NOT NULL,
                title        TEXT NOT NULL,
                due_date     TEXT,
                type         TEXT NOT NULL DEFAULT 'assignment',
                source       TEXT NOT NULL DEFAULT 'manual',
                completed    INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                notes        TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_items_child ON items(child);
            CREATE INDEX IF NOT EXISTS idx_items_due   ON items(due_date);
            CREATE INDEX IF NOT EXISTS idx_items_done  ON items(completed);
        """)
    conn.close()
    init_digests_table(db_path)


def create_item(
    db_path: str,
    child: str,
    title: str,
    item_type: str = "assignment",
    source: str = "manual",
    due_date: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    """Insert a new item and return its id."""
    if not child or not child.strip():
        raise ValueError("child must not be empty")
    if not title or not title.strip():
        raise ValueError("title must not be empty")
    conn = _connect(db_path)
    with conn:
        cursor = conn.execute(
            "INSERT INTO items (child, title, type, source, due_date, notes)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (child, title, item_type, source, due_date, notes or None),
        )
    item_id = cursor.lastrowid
    conn.close()
    return item_id


def update_item(db_path: str, item_id: int, **kwargs) -> bool:
    """Partial update. Pass completed=True/False to also set/clear completed_at."""
    updatable = {"child", "title", "type", "due_date", "notes"}
    fields: dict = {k: v for k, v in kwargs.items() if k in updatable}

    if "completed" in kwargs:
        fields["completed"] = 1 if kwargs["completed"] else 0
        fields["completed_at"] = (
            datetime.now().isoformat() if kwargs["completed"] else None
        )

    if not fields:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [item_id]
    conn = _connect(db_path)
    with conn:
        cursor = conn.execute(
            f"UPDATE items SET {set_clause} WHERE id = ?", values
        )
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def complete_item(db_path: str, item_id: int) -> bool:
    """Mark item completed and record timestamp."""
    return update_item(db_path, item_id, completed=True)


def list_items(
    db_path: str,
    child: Optional[str] = None,
    include_completed: bool = False,
) -> list[dict]:
    """Return items sorted: items with due_date first (ASC), then undated by created_at."""
    if not Path(db_path).exists():
        return []
    conditions: list[str] = []
    params: list = []
    if child:
        conditions.append("child = ?")
        params.append(child)
    if not include_completed:
        conditions.append("completed = 0")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    conn = _connect(db_path)
    rows = conn.execute(
        f"SELECT * FROM items {where}"
        " ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, created_at",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_item(db_path: str, item_id: int) -> bool:
    """Delete an item. Returns True if a row was removed."""
    conn = _connect(db_path)
    with conn:
        cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def get_item(db_path: str, item_id: int) -> Optional[dict]:
    """Return a single item by id, or None if not found."""
    if not Path(db_path).exists():
        return None
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT * FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def item_exists_for_email(
    db_path: str, child: str, title: str, due_date: Optional[str]
) -> bool:
    """True if an email-sourced item with this (child, title, due_date) already exists."""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT 1 FROM items"
        " WHERE child = ? AND title = ? AND due_date IS ? AND source = 'email'",
        (child, title, due_date),
    ).fetchone()
    conn.close()
    return row is not None


def init_digests_table(db_path: str) -> None:
    """Create digests table if it doesn't exist."""
    conn = _connect(db_path)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS digests (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                cards TEXT NOT NULL
            )
        """)
    conn.close()


def create_digest(db_path: str, title: str, cards: list[dict]) -> str:
    """Insert a digest and return its 8-char hex ID."""
    for _ in range(5):
        digest_id = os.urandom(4).hex()
        conn = _connect(db_path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO digests (id, created_at, title, cards)"
                    " VALUES (?, datetime('now'), ?, ?)",
                    (digest_id, title, json.dumps(cards)),
                )
            return digest_id
        except sqlite3.IntegrityError:
            continue
        finally:
            conn.close()
    raise RuntimeError("Failed to generate a unique digest ID after 5 attempts")


def get_digest(db_path: str, digest_id: str) -> Optional[dict]:
    """Return digest with parsed cards list, or None."""
    if not Path(db_path).exists():
        return None
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM digests WHERE id = ?", (digest_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["cards"] = json.loads(d["cards"])
    return d


def mark_digest_card_done(db_path: str, digest_id: str, card_index: int, done: bool) -> bool:
    """Toggle done state on a specific card. Returns False if not found or index invalid."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT cards FROM digests WHERE id = ?", (digest_id,)).fetchone()
        if not row:
            return False
        cards = json.loads(row["cards"])
        if card_index < 0 or card_index >= len(cards):
            return False
        cards[card_index]["done"] = done
        with conn:
            conn.execute(
                "UPDATE digests SET cards = ? WHERE id = ?",
                (json.dumps(cards), digest_id),
            )
        return True
    finally:
        conn.close()


def purge_old_digests(db_path: str, days: int = 7) -> int:
    """Delete digests older than `days`. Returns count deleted."""
    if not Path(db_path).exists():
        return 0
    conn = _connect(db_path)
    with conn:
        cursor = conn.execute(
            "DELETE FROM digests WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
    count = cursor.rowcount
    conn.close()
    return count
