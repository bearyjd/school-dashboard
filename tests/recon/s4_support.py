"""Helpers for the S4a tests (service layer, read model, schema v2). All data is SYNTHETIC."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Sequence

from recon.ingest_support import WED, make_db
from recon.report_support import ps, sgy

NOW = WED


def parent(name: str = "Dana"):
    from school_dashboard.assignments.service import Actor
    return Actor("parent", name)


def kid_actor(name: str = "Alex"):
    from school_dashboard.assignments.service import Actor
    return Actor("kid", name)


def open_conn(tmp_path: Path, seed: Sequence[dict[str, Any]] = (), **kw: Any) -> sqlite3.Connection:
    from school_dashboard.assignments import schema
    return schema.connect(make_db(tmp_path, {"seed": list(seed)}, **kw))


def kw(**over: Any) -> dict[str, Any]:
    return {"now": NOW, "no_school_rows": [], **over}


def essay_pair(**over: Any) -> list[dict[str, Any]]:
    """Two separate records for one assignment, one per source (ids 1 and 2)."""
    return [sgy("Essay", "Math 6", "s1", **over), ps("Essay", "Math 6", "p1", **over)]


def stored(conn: sqlite3.Connection, aid: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM assignments WHERE id = ?", (aid,)).fetchone()


def event_rows(conn: sqlite3.Connection, aid: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM assignment_events WHERE assignment_id = ? ORDER BY id", (aid,)).fetchall()


def link_set(conn: sqlite3.Connection, aid: int) -> set[tuple[str, str]]:
    return {(r["source"], r["external_key"]) for r in
            conn.execute("SELECT source, external_key FROM source_links WHERE assignment_id = ?", (aid,))}
