"""Persistence for the reconciliation tables. All SQL is parameterised; history is append-only."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator, Mapping

from school_dashboard.assignments.model import AssignmentState, LinkState


class BusyError(RuntimeError):
    """The database write lock could not be taken within the busy timeout."""


class NotMigratedError(RuntimeError):
    """The legacy items have not been migrated yet (recon_meta.migrated_at is absent)."""


class RepoError(RuntimeError):
    """A write did not have the expected effect."""


_ASSIGNMENT_SELECT = (
    "SELECT a.*, COALESCE(cs.paper_only, 0) AS paper_only_flag FROM assignments a "
    "LEFT JOIN class_settings cs ON cs.kid = a.kid AND cs.class_key = a.class_key"
)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT; ROLLBACK on any error; BusyError when the lock is not granted."""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc) or "busy" in str(exc):
            raise BusyError("database is busy") from exc
        raise
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _required_dt(value: str | None, column: str) -> datetime:
    parsed = _dt(value)
    if parsed is None:
        raise RepoError(f"assignments.{column} is NULL but the schema forbids it")
    return parsed


def _to_state(row: sqlite3.Row) -> AssignmentState:
    return AssignmentState(
        id=row["id"], kid=row["kid"], class_name=row["class_name"], class_key=row["class_key"] or "",
        title=row["title"], title_key=row["title_key"], due_date=_date(row["due_date"]), kind=row["kind"],
        stage=row["stage"], stage_since=_required_dt(row["stage_since"], "stage_since"), received_evidence=row["received_evidence"],
        portal_status=row["portal_status"], portal_conflict=bool(row["portal_conflict"]),
        closed_at=_dt(row["closed_at"]), paper_only=bool(row["paper_only_flag"]),
    )


# ---- assignments ----------------------------------------------------------

def insert_assignment(conn: sqlite3.Connection, state: AssignmentState, *, now: datetime,
                      notes: str | None = None, legacy_item_id: int | None = None,
                      legacy_action_id: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO assignments (kid, class_name, class_key, title, title_key, due_date, kind, stage,"
        " stage_since, received_evidence, portal_status, portal_conflict, notes, legacy_item_id,"
        " legacy_action_id, created_at, updated_at, closed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (state.kid, state.class_name, state.class_key, state.title, state.title_key, _iso(state.due_date),
         state.kind, state.stage, _iso(state.stage_since), state.received_evidence, state.portal_status,
         int(state.portal_conflict), notes, legacy_item_id, legacy_action_id, now.isoformat(),
         now.isoformat(), _iso(state.closed_at)))
    if cur.rowcount != 1 or not cur.lastrowid:
        raise RepoError("assignment insert had no effect")
    return int(cur.lastrowid)


def update_assignment(conn: sqlite3.Connection, state: AssignmentState, *, now: datetime) -> None:
    if state.id is None:
        raise ValueError("cannot update an assignment without an id")
    conn.execute(
        "UPDATE assignments SET class_name = ?, class_key = ?, title = ?, title_key = ?, due_date = ?,"
        " kind = ?, stage = ?, stage_since = ?, received_evidence = ?, portal_status = ?,"
        " portal_conflict = ?, closed_at = ?, updated_at = ? WHERE id = ?",
        (state.class_name, state.class_key, state.title, state.title_key, _iso(state.due_date), state.kind,
         state.stage, _iso(state.stage_since), state.received_evidence, state.portal_status,
         int(state.portal_conflict), _iso(state.closed_at), now.isoformat(), state.id))


def get_assignment(conn: sqlite3.Connection, assignment_id: int) -> AssignmentState | None:
    row = conn.execute(_ASSIGNMENT_SELECT + " WHERE a.id = ?", (assignment_id,)).fetchone()
    return _to_state(row) if row else None


def list_assignments(conn: sqlite3.Connection, kid: str | None = None,
                     include_closed: bool = False) -> list[AssignmentState]:
    rows = conn.execute(
        _ASSIGNMENT_SELECT + " WHERE (? IS NULL OR a.kid = ?) AND (? = 1 OR a.stage < 4) ORDER BY a.id",
        (kid, kid, int(include_closed))).fetchall()
    return [_to_state(r) for r in rows]


# ---- links ----------------------------------------------------------------

def upsert_link(conn: sqlite3.Connection, assignment_id: int, kid: str, link: LinkState, *,
                now: datetime, raw_status: str | None = None, raw_json: str = "{}") -> None:
    stamp = now.isoformat()
    conn.execute(
        "INSERT INTO source_links (assignment_id, source, kid_external, external_key, url, raw_status,"
        " norm_status, score, raw_json, first_seen, last_seen, absent_since, trusted_misses, live,"
        " last_miss_hash, via) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (source, kid_external) DO UPDATE SET assignment_id = excluded.assignment_id,"
        " url = excluded.url, raw_status = excluded.raw_status, norm_status = excluded.norm_status,"
        " score = excluded.score, raw_json = excluded.raw_json,"
        " last_seen = CASE WHEN excluded.live = 1 THEN excluded.last_seen ELSE source_links.last_seen END,"
        " absent_since = excluded.absent_since, trusted_misses = excluded.trusted_misses,"
        " live = excluded.live, last_miss_hash = excluded.last_miss_hash, via = excluded.via",
        (assignment_id, link.source, f"{kid}|{link.external_key}", link.external_key, link.url, raw_status,
         link.norm_status, link.score, raw_json, stamp, stamp, _iso(link.absent_since), link.trusted_misses,
         int(link.live), link.last_miss_hash, link.via))


def links_for(conn: sqlite3.Connection, assignment_id: int) -> list[LinkState]:
    rows = conn.execute("SELECT * FROM source_links WHERE assignment_id = ? ORDER BY id",
                        (assignment_id,)).fetchall()
    return [LinkState(source=r["source"], external_key=r["external_key"], norm_status=r["norm_status"],
                      score=r["score"], url=r["url"], live=bool(r["live"]),
                      trusted_misses=r["trusted_misses"], absent_since=_date(r["absent_since"]),
                      last_miss_hash=r["last_miss_hash"], via=r["via"]) for r in rows]


# ---- events (append-only) -------------------------------------------------

def append_event(conn: sqlite3.Connection, assignment_id: int, *, at: datetime, setter: str, kind: str,
                 actor: str | None = None, source: str | None = None, from_value: str | None = None,
                 to_value: str | None = None, detail: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO assignment_events (assignment_id, at, setter, actor, kind, source, from_value,"
        " to_value, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (assignment_id, at.isoformat(), setter, actor, kind, source, from_value, to_value, detail))
    if not cur.lastrowid:
        raise RepoError("assignment event insert had no effect")
    return int(cur.lastrowid)


def events_for(conn: sqlite3.Connection, assignment_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM assignment_events WHERE assignment_id = ? ORDER BY id",
                        (assignment_id,)).fetchall()
    return [dict(r) for r in rows]


# ---- meta -----------------------------------------------------------------

def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM recon_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO recon_meta (key, value) VALUES (?, ?)"
                 " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


def require_migrated(conn: sqlite3.Connection) -> None:
    if get_meta(conn, "migrated_at") is None:
        raise NotMigratedError("legacy items are not migrated; run: school-state assignments migrate")


# ---- push outbox ----------------------------------------------------------

def enqueue_push(conn: sqlite3.Connection, assignment_id: int, flag: str, occurrence_at: datetime,
                 reason: str) -> None:
    status = "suppressed" if reason == "baseline" else "pending"
    conn.execute(
        "INSERT OR IGNORE INTO push_outbox (assignment_id, flag, occurrence_at, reason, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (assignment_id, flag, occurrence_at.isoformat(), reason, status, occurrence_at.isoformat()))


def pending_pushes(conn: sqlite3.Connection) -> list[Mapping[str, Any]]:
    rows = conn.execute("SELECT * FROM push_outbox WHERE status IN ('pending', 'failed') ORDER BY id")
    return [dict(r) for r in rows.fetchall()]


def mark_push(conn: sqlite3.Connection, push_id: int, status: str, *, now: datetime,
              error: str | None = None) -> None:
    if status == "sent":
        conn.execute("UPDATE push_outbox SET status = 'sent', sent_at = ?, attempts = attempts + 1"
                     " WHERE id = ?", (now.isoformat(), push_id))
    elif status == "failed":
        conn.execute("UPDATE push_outbox SET status = 'failed', attempts = attempts + 1, last_error = ?"
                     " WHERE id = ?", (error, push_id))
    else:
        raise ValueError(f"unsupported push status: {status}")
