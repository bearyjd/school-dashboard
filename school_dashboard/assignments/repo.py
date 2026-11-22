"""Persistence for the reconciliation tables. All SQL is parameterised; history is append-only."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator, Mapping, Sequence

from school_dashboard import watchdog
from school_dashboard.assignments.dates import parse_date, parse_dt
from school_dashboard.assignments.model import AssignmentRecord, AssignmentState, LinkRecord, LinkState


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


def _unreadable(raw: str | None, parsed: object) -> bool:
    return bool(raw) and parsed is None


def _to_state(row: sqlite3.Row) -> AssignmentState:
    """A record from a stored row. Unreadable dates never raise: a due date or closed_at becomes None, stage_since
    falls back to created_at, then updated_at, then now; any such row is flagged `data_problem`."""
    tz = watchdog.get_tz()
    due, closed = parse_date(row["due_date"]), parse_dt(row["closed_at"], tz)
    dismissed_raw = row["dismissed_at"] if "dismissed_at" in row.keys() else None  # read-only v2 databases
    dismissed = parse_dt(dismissed_raw, tz)
    since = parse_dt(row["stage_since"], tz)
    bad = _unreadable(row["due_date"], due) or _unreadable(row["closed_at"], closed) or since is None
    if since is None:
        since = parse_dt(row["created_at"], tz) or parse_dt(row["updated_at"], tz) or datetime.now(tz)
    return AssignmentState(
        id=row["id"], kid=row["kid"], class_name=row["class_name"], class_key=row["class_key"] or "",
        title=row["title"], title_key=row["title_key"], due_date=due, kind=row["kind"],
        stage=row["stage"], stage_since=since, received_evidence=row["received_evidence"],
        portal_status=row["portal_status"], portal_conflict=bool(row["portal_conflict"]),
        closed_at=closed, paper_only=bool(row["paper_only_flag"]), dismissed_at=dismissed,
        data_problem=bad or _unreadable(dismissed_raw, dismissed),
    )


# ---- assignments ----------------------------------------------------------

def insert_assignment(conn: sqlite3.Connection, state: AssignmentState, *, now: datetime,
                      notes: str | None = None, legacy_item_id: int | None = None,
                      legacy_action_id: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO assignments (kid, class_name, class_key, title, title_key, due_date, kind, stage,"
        " stage_since, received_evidence, portal_status, portal_conflict, notes, legacy_item_id,"
        " legacy_action_id, created_at, updated_at, closed_at, dismissed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (state.kid, state.class_name, state.class_key, state.title, state.title_key, _iso(state.due_date),
         state.kind, state.stage, _iso(state.stage_since), state.received_evidence, state.portal_status,
         int(state.portal_conflict), notes, legacy_item_id, legacy_action_id, now.isoformat(),
         now.isoformat(), _iso(state.closed_at), _iso(state.dismissed_at)))
    if cur.rowcount != 1 or not cur.lastrowid:
        raise RepoError("assignment insert had no effect")
    return int(cur.lastrowid)


def update_assignment(conn: sqlite3.Connection, state: AssignmentState, *, now: datetime) -> None:
    if state.id is None:
        raise ValueError("cannot update an assignment without an id")
    conn.execute(
        "UPDATE assignments SET class_name = ?, class_key = ?, title = ?, title_key = ?, due_date = ?,"
        " kind = ?, stage = ?, stage_since = ?, received_evidence = ?, portal_status = ?,"
        " portal_conflict = ?, closed_at = ?, dismissed_at = ?, updated_at = ? WHERE id = ?",
        (state.class_name, state.class_key, state.title, state.title_key, _iso(state.due_date), state.kind,
         state.stage, _iso(state.stage_since), state.received_evidence, state.portal_status,
         int(state.portal_conflict), _iso(state.closed_at), _iso(state.dismissed_at), now.isoformat(), state.id))


def get_assignment(conn: sqlite3.Connection, assignment_id: int) -> AssignmentState | None:
    row = conn.execute(_ASSIGNMENT_SELECT + " WHERE a.id = ?", (assignment_id,)).fetchone()
    return _to_state(row) if row else None


def list_assignments(conn: sqlite3.Connection, kid: str | None = None,
                     include_closed: bool = False) -> list[AssignmentState]:
    rows = conn.execute(
        _ASSIGNMENT_SELECT + " WHERE (? IS NULL OR a.kid = ?) AND (? = 1 OR a.stage < 4) AND a.merged_into IS NULL ORDER BY a.id",
        (kid, kid, int(include_closed))).fetchall()
    states = [_to_state(r) for r in rows]
    return states if include_closed else [s for s in states if not s.inactive]


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


def _to_link(r: sqlite3.Row) -> LinkState:
    return LinkState(source=r["source"], external_key=r["external_key"], norm_status=r["norm_status"],
                     score=r["score"], url=r["url"], live=bool(r["live"]),
                     trusted_misses=r["trusted_misses"], absent_since=parse_date(r["absent_since"]),
                     last_miss_hash=r["last_miss_hash"], via=r["via"])


def links_for(conn: sqlite3.Connection, assignment_id: int) -> list[LinkState]:
    rows = conn.execute("SELECT * FROM source_links WHERE assignment_id = ? ORDER BY id",
                        (assignment_id,)).fetchall()
    return [_to_link(r) for r in rows]


def link_index(conn: sqlite3.Connection, kid: str) -> dict[tuple[str, str, str], int]:
    """(source, kid, external_key) -> assignment id for every link of the kid's assignments."""
    rows = conn.execute(
        "SELECT sl.source, sl.external_key, sl.assignment_id FROM source_links sl"
        " JOIN assignments a ON a.id = sl.assignment_id WHERE a.kid = ?", (kid,)).fetchall()
    return {(r["source"], kid, r["external_key"]): r["assignment_id"] for r in rows}


def kid_links(conn: sqlite3.Connection, kid: str, source: str) -> list[tuple[int, LinkState]]:
    rows = conn.execute(
        "SELECT sl.* FROM source_links sl JOIN assignments a ON a.id = sl.assignment_id"
        " WHERE a.kid = ? AND sl.source = ? ORDER BY sl.id", (kid, source)).fetchall()
    return [(r["assignment_id"], _to_link(r)) for r in rows]


def update_link_absence(conn: sqlite3.Connection, assignment_id: int, link: LinkState) -> None:
    """Write only the liveness columns (raw payload, url and status stay as the last sighting left them)."""
    conn.execute(
        "UPDATE source_links SET live = ?, absent_since = ?, trusted_misses = ?, last_miss_hash = ?"
        " WHERE assignment_id = ? AND source = ? AND external_key = ?",
        (int(link.live), _iso(link.absent_since), link.trusted_misses, link.last_miss_hash,
         assignment_id, link.source, link.external_key))


# ---- stored flags and class settings -----------------------------------------

def stored_flags(conn: sqlite3.Connection, kid: str) -> dict[int, str | None]:
    rows = conn.execute("SELECT id, flag FROM assignments WHERE kid = ?", (kid,)).fetchall()
    return {r["id"]: r["flag"] for r in rows}


def set_flag(conn: sqlite3.Connection, assignment_id: int, flag: str | None, since: datetime) -> None:
    conn.execute("UPDATE assignments SET flag = ?, flag_since = ? WHERE id = ?",
                 (flag, since.isoformat(), assignment_id))


def class_settings_rows(conn: sqlite3.Connection, kid: str) -> list[tuple[str, int | None, str | None]]:
    """(class_key, grace_school_days, aliases JSON) for the kid's configured classes."""
    rows = conn.execute("SELECT class_key, grace_school_days, aliases FROM class_settings WHERE kid = ?",
                        (kid,)).fetchall()
    return [(r["class_key"], r["grace_school_days"], r["aliases"]) for r in rows]


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


# ---- read model, merge and settings helpers -------------------------------------

_DUPLICATE_SQL = (
    "SELECT e.assignment_id FROM assignment_events e WHERE e.kind = 'merge' AND e.detail LIKE 'possible_duplicate%'"
    " AND NOT EXISTS (SELECT 1 FROM assignment_events m WHERE m.assignment_id = e.assignment_id"
    " AND m.kind IN ('merge', 'split') AND m.detail = 'manual' AND m.id > e.id)")


def assignment_records(conn: sqlite3.Connection, kid: str | None = None,
                       include_closed: bool = True) -> list[AssignmentRecord]:
    dups = {r["assignment_id"] for r in conn.execute(_DUPLICATE_SQL)}
    return [AssignmentRecord(a, a.id in dups) for a in list_assignments(conn, kid, include_closed)]


def link_records(conn: sqlite3.Connection, assignment_ids: Sequence[int]) -> dict[int, list[LinkRecord]]:
    out: dict[int, list[LinkRecord]] = {i: [] for i in assignment_ids}
    for aid in assignment_ids:
        rows = conn.execute("SELECT * FROM source_links WHERE assignment_id = ? ORDER BY id", (aid,)).fetchall()
        out[aid] = [LinkRecord(r["id"], r["raw_status"], _to_link(r)) for r in rows]
    return out


def link_raw_json(conn: sqlite3.Connection, link_id: int) -> str | None:
    row = conn.execute("SELECT raw_json FROM source_links WHERE id = ?", (link_id,)).fetchone()
    return row["raw_json"] if row else None


def move_links(conn: sqlite3.Connection, from_id: int, to_id: int) -> None:
    """The only place whole link sets move (manual merge)."""
    conn.execute("UPDATE source_links SET assignment_id = ? WHERE assignment_id = ?", (to_id, from_id))


def move_link(conn: sqlite3.Connection, link_id: int, to_id: int) -> None:
    conn.execute("UPDATE source_links SET assignment_id = ? WHERE id = ?", (to_id, link_id))


def merged_into(conn: sqlite3.Connection, assignment_id: int) -> int | None:
    row = conn.execute("SELECT merged_into FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    return row["merged_into"] if row else None


def mark_merged(conn: sqlite3.Connection, source_id: int, into_id: int, now: datetime) -> None:
    conn.execute("UPDATE assignments SET merged_into = ?, closed_at = ?, updated_at = ?, flag = NULL, flag_since = ?"
                 " WHERE id = ?", (into_id, now.isoformat(), now.isoformat(), now.isoformat(), source_id))


def kid_has_assignments(conn: sqlite3.Connection, kid: str) -> bool:
    return conn.execute("SELECT 1 FROM assignments WHERE kid = ? LIMIT 1", (kid,)).fetchone() is not None


def class_grace(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    rows = conn.execute("SELECT kid, class_key, grace_school_days FROM class_settings"
                        " WHERE grace_school_days IS NOT NULL").fetchall()
    return {(r["kid"], r["class_key"]): r["grace_school_days"] for r in rows}


def aliases_json(conn: sqlite3.Connection, kid: str, class_key: str) -> str | None:
    row = conn.execute("SELECT aliases FROM class_settings WHERE kid = ? AND class_key = ?",
                       (kid, class_key)).fetchone()
    return row["aliases"] if row else None


def put_class_settings(conn: sqlite3.Connection, kid: str, class_key: str, *, grace: int | None, paper_only: bool,
                       aliases: str | None, teacher_name: str | None, teacher_email: str | None) -> None:
    conn.execute(
        "INSERT INTO class_settings (kid, class_key, grace_school_days, paper_only, aliases, teacher_name,"
        " teacher_email) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (kid, class_key) DO UPDATE SET"
        " grace_school_days = excluded.grace_school_days, paper_only = excluded.paper_only,"
        " aliases = excluded.aliases, teacher_name = excluded.teacher_name, teacher_email = excluded.teacher_email",
        (kid, class_key, grace, int(paper_only), aliases, teacher_name, teacher_email))


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

DEBOUNCED = "debounced"  # push_outbox.last_error marker of a push skipped by the debounce

def enqueue_push(conn: sqlite3.Connection, assignment_id: int, flag: str, occurrence_at: datetime,
                 reason: str) -> None:
    status = "suppressed" if reason == "baseline" else "pending"
    conn.execute(
        "INSERT OR IGNORE INTO push_outbox (assignment_id, flag, occurrence_at, reason, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (assignment_id, flag, occurrence_at.isoformat(), reason, status, occurrence_at.isoformat()))


def record_debounced_push(conn: sqlite3.Connection, assignment_id: int, flag: str, at: datetime) -> bool:
    """Count a push skipped by the debounce as a suppressed row; True only when the row was inserted.

    `recent_push_exists` ignores it."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO push_outbox (assignment_id, flag, occurrence_at, reason, status, created_at, last_error)"
        " VALUES (?, ?, ?, 'transition', 'suppressed', ?, ?)",
        (assignment_id, flag, at.isoformat(), at.isoformat(), DEBOUNCED))
    return cur.rowcount == 1


def recent_push_exists(conn: sqlite3.Connection, assignment_id: int, flag: str, since: datetime) -> bool:
    """A push row for (assignment, flag) created after `since`; baseline-suppressed rows do not count."""
    return conn.execute(
        "SELECT 1 FROM push_outbox WHERE assignment_id = ? AND flag = ? AND julianday(created_at) > julianday(?)"
        " AND NOT (status = 'suppressed' AND (reason = 'baseline' OR COALESCE(last_error, '') = ?))"
        " LIMIT 1",
        (assignment_id, flag, since.isoformat(), DEBOUNCED)).fetchone() is not None


def pending_pushes(conn: sqlite3.Connection) -> list[Mapping[str, Any]]:
    rows = conn.execute("SELECT * FROM push_outbox WHERE status IN ('pending', 'failed') ORDER BY id")
    return [dict(r) for r in rows.fetchall()]


def mark_push(conn: sqlite3.Connection, push_id: int, status: str, *, now: datetime,
              error: str | None = None) -> None:
    if status == "sent":
        conn.execute("UPDATE push_outbox SET status = 'sent', sent_at = ?, attempts = attempts + 1"
                     " WHERE id = ? AND status IN ('pending', 'failed')", (now.isoformat(), push_id))
    elif status == "failed":
        conn.execute("UPDATE push_outbox SET status = 'failed', attempts = attempts + 1, last_error = ?"
                     " WHERE id = ? AND status IN ('pending', 'failed')", (error, push_id))
    else:
        raise ValueError(f"unsupported push status: {status}")


# ---- API read helpers --------------------------------------------------------

def assignment_kids(conn: sqlite3.Connection) -> list[str]:
    """Kids that have at least one live (not merged away) assignment."""
    rows = conn.execute("SELECT DISTINCT kid FROM assignments WHERE merged_into IS NULL ORDER BY kid").fetchall()
    return [r["kid"] for r in rows]


def class_overview(conn: sqlite3.Connection, kid: str) -> list[dict[str, Any]]:
    """The kid's classes: every configured class plus every class seen on a live assignment."""
    names = {r["class_key"]: r["class_name"] for r in conn.execute(
        "SELECT class_key, MIN(class_name) AS class_name FROM assignments"
        " WHERE kid = ? AND merged_into IS NULL AND class_key != '' GROUP BY class_key", (kid,))}
    cfg = {r["class_key"]: r for r in conn.execute(
        "SELECT class_key, grace_school_days, paper_only, aliases, teacher_name, teacher_email"
        " FROM class_settings WHERE kid = ?", (kid,))}
    out: list[dict[str, Any]] = []
    for key in sorted({*names, *cfg}):
        row = cfg.get(key)
        out.append({"class_key": key, "class_name": names.get(key, key),
                    "grace_school_days": row["grace_school_days"] if row else None,
                    "paper_only": bool(row["paper_only"]) if row else False,
                    "aliases": row["aliases"] if row else None,
                    "teacher_name": row["teacher_name"] if row else None,
                    "teacher_email": row["teacher_email"] if row else None})
    return out


def calendar_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Calendar rows (date, type, child) for school-day arithmetic; [] when the events table is not there."""
    try:
        return [dict(r) for r in conn.execute("SELECT date, type, child FROM events")]
    except sqlite3.OperationalError:
        return []
