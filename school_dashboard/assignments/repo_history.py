"""Persistence helpers for event history, merge bookkeeping and the class-settings audit log."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Sequence


def latest_manual_stage(conn: sqlite3.Connection, assignment_id: int, *, ignore_details: Sequence[str] = ()) -> int:
    """Stage (1-3) from the latest parent/kid stage event; 4 is never taken from manual history."""
    marks = ", ".join("?" for _ in ignore_details)
    skip = f" AND COALESCE(detail, '') NOT IN ({marks})" if ignore_details else ""
    row = conn.execute(
        "SELECT to_value FROM assignment_events WHERE assignment_id = ? AND kind = 'stage'"  # marks are "?" only
        f" AND setter IN ('parent', 'kid'){skip} ORDER BY id DESC LIMIT 1", (assignment_id, *ignore_details)).fetchone()
    return int(row["to_value"]) if row and row["to_value"] in ("2", "3") else 1



def split_partner_exists(conn: sqlite3.Connection, a: int, b: int) -> bool:
    """True when a manual split ever separated these two records (either direction)."""
    return conn.execute(
        "SELECT 1 FROM assignment_events WHERE kind = 'split' AND ((assignment_id = ? AND detail LIKE ?)"
        " OR (assignment_id = ? AND detail LIKE ?)) LIMIT 1",
        (a, f"manual with={b} %", b, f"manual with={a} %")).fetchone() is not None



def merged_sources(conn: sqlite3.Connection, into_id: int) -> list[int]:
    """Records merged into `into_id` (newest merge first)."""
    rows = conn.execute("SELECT id FROM assignments WHERE merged_into = ? ORDER BY id DESC", (into_id,)).fetchall()
    return [r["id"] for r in rows]



def merge_link_ids(conn: sqlite3.Connection, source_id: int) -> frozenset[int]:
    """Link ids named by the source's merge events (`manual links=1,2`)."""
    out: set[int] = set()
    for row in conn.execute("SELECT detail FROM assignment_events WHERE assignment_id = ? AND kind = 'merge'",
                            (source_id,)):
        text = str(row["detail"] or "")
        if "links=" in text:
            out.update(int(x) for x in text.split("links=", 1)[1].split(",") if x.strip().isdigit())
    return frozenset(out)



def reopen_merged(conn: sqlite3.Connection, source_id: int, now: datetime) -> None:
    conn.execute("UPDATE assignments SET merged_into = NULL, closed_at = NULL, updated_at = ? WHERE id = ?",
                 (now.isoformat(), source_id))



def class_settings_row(conn: sqlite3.Connection, kid: str, class_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT grace_school_days, paper_only, aliases, teacher_name, teacher_email FROM class_settings"
        " WHERE kid = ? AND class_key = ?", (kid, class_key)).fetchone()
    return dict(row) if row else None



def append_class_event(conn: sqlite3.Connection, *, at: datetime, kid: str, class_key: str, setter: str,
                       actor: str | None, changes: str) -> None:
    conn.execute("INSERT INTO class_settings_events (at, kid, class_key, setter, actor, changes)"
                 " VALUES (?, ?, ?, ?, ?, ?)", (at.isoformat(), kid, class_key, setter, actor, changes))



def class_events(conn: sqlite3.Connection, kid: str, limit: int = 20) -> list[dict[str, Any]]:
    """The kid's newest class-settings changes; [] when a v2 database has no audit table yet."""
    try:
        rows = conn.execute("SELECT at, class_key, setter, actor, changes FROM class_settings_events"
                            " WHERE kid = ? ORDER BY id DESC LIMIT ?", (kid, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]

