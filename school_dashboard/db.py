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
    conn.commit()
    conn.close()


def create_item(
    db_path: str,
    child: str,
    title: str,
    type: str = "assignment",
    source: str = "manual",
    due_date: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    """Insert a new item and return its id."""
    conn = _connect(db_path)
    cursor = conn.execute(
        "INSERT INTO items (child, title, type, source, due_date, notes)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (child, title, type, source, due_date, notes or None),
    )
    conn.commit()
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
    cursor = conn.execute(
        f"UPDATE items SET {set_clause} WHERE id = ?", values
    )
    conn.commit()
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
    cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


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
