"""Migration of legacy `items` and state `action_items` into reconciliation assignments (plan 3.7, 10.8)."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from school_dashboard import maintenance, paths, state as state_mod
from school_dashboard.assignments import migrate_backup, repo, schema
from school_dashboard.assignments.migrate_plan import UNKNOWN_KID, MigrationPlan, PlannedRecord, plan_migration
from school_dashboard.assignments.model import LinkState

logger = logging.getLogger(__name__)

MAX_STATE_BYTES = 50 * 1024 * 1024

__all__ = ["MigrationReport", "MigrationRefused", "MigrationError", "MigrationInputError",
           "plan_migration", "run", "export"]


@dataclass(frozen=True)
class MigrationReport:
    dry_run: bool
    rows_in: int
    created: int
    merged: tuple[Mapping[str, Any], ...]
    per_kid: Mapping[str, int]
    warnings: tuple[str, ...]
    backup_dir: str | None
    items_sha_before: str
    items_sha_after: str
    already_migrated: int
    skipped: int = 0
    skipped_keys: tuple[str, ...] = ()


class MigrationRefused(RuntimeError):
    """Maintenance mode is off or a sync is in flight."""


class MigrationError(RuntimeError):
    """Verification mismatch, backup failure or a failed write (everything rolled back)."""


class MigrationInputError(ValueError):
    """The database or state file is missing or malformed."""


_ITEMS_SQL = "SELECT * FROM items ORDER BY id"


def _rows_sha(rows: Sequence[Any]) -> str:
    return hashlib.sha256(json.dumps([list(r) for r in rows], default=str).encode()).hexdigest()


def _items_sha(conn: sqlite3.Connection) -> str:
    return _rows_sha(conn.execute(_ITEMS_SQL).fetchall())


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (name,)).fetchone() is not None


def _check_action_ids(actions: Sequence[Mapping[str, Any]]) -> None:
    """Every action item needs a distinct non-empty str or int id (counts-only messages, before any write)."""
    ids = [a["id"] for a in actions]
    bad = sum(1 for i in ids if isinstance(i, bool) or not isinstance(i, (str, int)) or not str(i).strip())
    if bad:
        raise MigrationInputError(f"state file has {bad} action item(s) with a missing, empty or invalid id")
    dupes = len(ids) - len({str(i) for i in ids})
    if dupes:
        raise MigrationInputError(f"state file has {dupes} action item(s) with a duplicate id")


def _load_action_items(state_path: str) -> tuple[list[dict[str, Any]], str]:
    """The state file's action_items and the sha256 of the exact bytes they were parsed from."""
    try:
        with Path(state_path).open("rb") as fh:
            raw = fh.read(MAX_STATE_BYTES + 1)  # bounded read: no separate size check that could go stale
    except OSError as exc:
        raise MigrationInputError(f"state file unreadable ({type(exc).__name__})") from exc
    if len(raw) > MAX_STATE_BYTES:
        raise MigrationInputError("state file is too large to migrate")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise MigrationInputError(f"state file unreadable ({type(exc).__name__})") from exc
    actions = data.get("action_items", []) if isinstance(data, dict) else None
    if not isinstance(actions, list) or not all(isinstance(a, dict) and "id" in a for a in actions):
        raise MigrationInputError("state file has no valid action_items list")
    _check_action_ids(actions)
    return actions, hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class _Snapshot:
    items: list[dict[str, Any]]
    item_ids_done: frozenset[int]
    action_ids_done: frozenset[str]
    migrated: bool
    sha: str


def _snapshot(db_path: str) -> _Snapshot:
    if not Path(db_path).is_file():
        raise MigrationInputError("database file not found")
    try:
        conn = schema.connect(db_path, readonly=True)
    except sqlite3.Error as exc:
        raise MigrationInputError(f"database unreadable ({type(exc).__name__})") from exc
    try:
        if not _has_table(conn, "items"):
            raise MigrationInputError("database has no items table")
        rows = conn.execute(_ITEMS_SQL).fetchall()  # the plan rows and their sha come from this one read
        items = [dict(r) for r in rows]
        sha = _rows_sha(rows)
        if not _has_table(conn, "assignments"):
            return _Snapshot(items, frozenset(), frozenset(), False, sha)
        ids = frozenset(r[0] for r in conn.execute(
            "SELECT legacy_item_id FROM assignments WHERE legacy_item_id IS NOT NULL"))
        acts = frozenset(r[0] for r in conn.execute(
            "SELECT legacy_action_id FROM assignments WHERE legacy_action_id IS NOT NULL"))
        migrated = _has_table(conn, "recon_meta") and repo.get_meta(conn, "migrated_at") is not None
        return _Snapshot(items, ids, acts, migrated, sha)
    except sqlite3.Error as exc:
        raise MigrationInputError(f"database unreadable ({type(exc).__name__})") from exc
    finally:
        conn.close()


def _roster_warnings(per_kid: Mapping[str, int]) -> tuple[str, ...]:
    roster = {state_mod.canonical_name(n) for n in state_mod.get_children()}
    if not roster:
        return ()
    off = [k for k in per_kid if k not in roster and k != UNKNOWN_KID]  # '(unknown)' has its own warning
    if off:
        logger.warning("%d kid name(s) not on the roster were kept verbatim", len(off))
    return tuple(f"kid {k!r} is not on the roster; {per_kid[k]} record(s) kept verbatim" for k in off)


def _write_record(conn: sqlite3.Connection, rec: PlannedRecord, now: datetime) -> None:
    aid = repo.insert_assignment(conn, rec.state, now=now, notes=rec.notes,
                                 legacy_item_id=rec.legacy_item_id, legacy_action_id=rec.legacy_action_id)
    for link in rec.links:
        repo.upsert_link(conn, aid, rec.state.kid,
                         LinkState(source=link.source, external_key=link.external_key, norm_status="none",
                                   via="manual"),
                         now=now, raw_status="", raw_json=link.raw_json)
    if rec.state.stage == 4 and rec.state.closed_at is not None:
        repo.append_event(conn, aid, at=rec.state.closed_at, setter="migration", kind="stage",
                          from_value="1", to_value="4", source=rec.links[0].source)


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # table is a constant


@dataclass(frozen=True)
class _Outcome:
    written: tuple[PlannedRecord, ...]
    skipped: tuple[PlannedRecord, ...]
    sha_after: str


def _write_each(conn: sqlite3.Connection, plan: MigrationPlan, now: datetime) -> _Outcome:
    """Write every record inside its own SAVEPOINT; a row the database rejects is rolled back and left out."""
    written: list[PlannedRecord] = []
    skipped: list[PlannedRecord] = []
    for rec in plan.records:
        conn.execute("SAVEPOINT rec")
        try:
            _write_record(conn, rec, now)
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK TO rec")
            skipped.append(rec)
        else:
            written.append(rec)
        finally:
            conn.execute("RELEASE rec")
    return _Outcome(tuple(written), tuple(skipped), "")


def _link_keys(recs: Sequence[PlannedRecord]) -> set[str]:
    return {link.external_key for r in recs for link in r.links}


def _verify(conn: sqlite3.Connection, out: _Outcome, before: tuple[int, int], expected_keys: set[str],
            sha_before: str) -> str:
    after = (_count(conn, "assignments"), _count(conn, "source_links"))
    links = sum(len(r.links) for r in out.written)
    if after[0] - before[0] != len(out.written) or after[1] - before[1] != links:
        raise MigrationError("row counts in the database do not match the plan")
    present = {r[0] for r in conn.execute("SELECT external_key FROM source_links")}
    if (expected_keys - _link_keys(out.skipped)) - present:
        raise MigrationError("some legacy rows have no assignment link in the database")
    sha_after = _items_sha(conn)
    if sha_after != sha_before:
        raise MigrationError("items table changed during migration")
    return sha_after


def _state_sha(state_path: str) -> str:
    return hashlib.sha256(Path(state_path).read_bytes()).hexdigest()


def _write_plan(db_path: str, plan: MigrationPlan, expected_keys: set[str], sha_before: str, now: datetime,
                guard: Callable[[], None]) -> _Outcome:
    """The one write path (real run and dry-run rehearsal): schema, per-record savepoints, verification."""
    conn = None
    try:
        conn = schema.connect(db_path)
        with repo.transaction(conn):
            guard()  # the gate may have changed since the plan was made; the write lock is held now
            schema.ensure_schema(conn)
            before = (_count(conn, "assignments"), _count(conn, "source_links"))
            if _items_sha(conn) != sha_before:
                raise MigrationError("items table changed since the plan was made")
            out = _write_each(conn, plan, now)
            sha_after = _verify(conn, out, before, expected_keys, sha_before)
            if out.written or not plan.records:  # all rows rejected: stay un-migrated so ingest stays gated
                repo.set_meta(conn, "migrated_at", now.isoformat())
        return dataclasses.replace(out, sha_after=sha_after)
    except (sqlite3.Error, repo.RepoError, repo.BusyError, OSError) as exc:
        raise MigrationError(f"migration rolled back ({type(exc).__name__})") from exc
    finally:
        if conn is not None:
            conn.close()


def _apply(db_path: str, state_path: str, plan: MigrationPlan, expected_keys: set[str], sha_before: str,
           state_sha: str, now: datetime) -> _Outcome:
    def guard() -> None:
        _preflight()
        if _state_sha(state_path) != state_sha:
            raise MigrationError("state file changed since the plan was made")
    return _write_plan(db_path, plan, expected_keys, sha_before, now, guard)


def _rehearse(db_path: str, plan: MigrationPlan, expected_keys: set[str], sha_before: str,
              now: datetime) -> _Outcome:
    """Dry run: the real write path on a private scratch copy of the database (removed afterwards)."""
    try:
        with migrate_backup.scratch_copy(db_path) as copy_path:
            return _write_plan(copy_path, plan, expected_keys, sha_before, now, lambda: None)
    except (migrate_backup.BackupError, sqlite3.Error, OSError) as exc:
        raise MigrationError(f"dry run could not copy the database ({type(exc).__name__})") from exc


def _preflight() -> None:
    if not maintenance.is_active():
        raise MigrationRefused("maintenance mode is not active; run: school-state transition maintenance on")
    if maintenance.sync_in_flight():
        raise MigrationRefused("a sync appears to be running; wait for it to finish and retry")


def _skip_warning(out: _Outcome) -> tuple[str, ...]:
    n = sum(len(r.links) for r in out.skipped)
    if not n:
        return ()
    logger.warning("%d rows could not be migrated and were left in the legacy store", n)
    return (f"{n} rows could not be migrated and were left in the legacy store",)


def _report(plan: MigrationPlan, out: _Outcome | None, snap: _Snapshot, already: int, rows_in: int,
            dry_run: bool, backup_dir: str | None) -> MigrationReport:
    written = plan.records if out is None else out.written
    skipped = () if out is None else out.skipped
    skipped_keys = {link.external_key for r in skipped for link in r.links}
    merged = tuple(m for m in plan.merged if m["action_key"] not in skipped_keys)
    per_kid = dict(sorted(Counter(r.state.kid for r in written).items()))
    warnings = plan.warnings + _roster_warnings(per_kid) + (_skip_warning(out) if out else ())
    return MigrationReport(dry_run, rows_in, len(written), merged, per_kid, warnings, backup_dir, snap.sha,
                           snap.sha if out is None else out.sha_after, already,
                           len(skipped_keys), tuple(sorted(skipped_keys)))


def run(db_path: str, state_path: str, *, dry_run: bool, now: datetime,
        archive_dir: str | None = None) -> MigrationReport:
    if not dry_run:
        _preflight()
    actions, state_sha = _load_action_items(state_path)
    snap = _snapshot(db_path)
    todo_items = [i for i in snap.items if i["id"] not in snap.item_ids_done]
    todo_actions = [a for a in actions if str(a["id"]) not in snap.action_ids_done]
    plan = plan_migration(todo_items, todo_actions, now=now, resolve_kid=state_mod.canonical_name)
    rows_in = len(snap.items) + len(actions)
    already = (len(snap.item_ids_done & {i["id"] for i in snap.items})
               + len(snap.action_ids_done & {str(a["id"]) for a in actions}))
    if rows_in != len(plan.records) + len(plan.merged) + already:
        raise MigrationError("row accounting invariant violated: rows in != created + merged + already migrated")
    out, backup_dir = None, None
    if plan.records or not snap.migrated:
        keys = {f"item:{i['id']}" for i in snap.items} | {f"legacy-action:{a['id']}" for a in actions}
        if dry_run:
            out = _rehearse(db_path, plan, keys, snap.sha, now)
        else:
            root = Path(archive_dir) if archive_dir else paths.state_dir() / "archive"
            try:
                backup_dir = migrate_backup.make_backup(db_path, state_path, root, now)
            except migrate_backup.BackupError as exc:
                raise MigrationError(str(exc)) from exc
            out = _apply(db_path, state_path, plan, keys, snap.sha, state_sha, now)
    rep = _report(plan, out, snap, already, rows_in, dry_run, backup_dir)
    logger.info("migration %s: rows_in=%d created=%d merged=%d already=%d skipped=%d",
                "dry-run" if dry_run else "done", rows_in, rep.created, len(rep.merged), already, rep.skipped)
    return rep


_EXPORT_TABLES: Sequence[tuple[str, str]] = (
    ("assignments", "SELECT * FROM assignments ORDER BY id"),
    ("source_links", "SELECT * FROM source_links ORDER BY id"),
    ("events", "SELECT * FROM assignment_events ORDER BY id"),
)


def export(db_path: str) -> Mapping[str, Any]:
    """Read-only dump of assignments, source links, events and meta."""
    if not Path(db_path).is_file():
        raise MigrationInputError("database file not found")
    try:
        conn = schema.connect(db_path, readonly=True)
        try:
            out: dict[str, Any] = {name: [dict(r) for r in conn.execute(sql)] for name, sql in _EXPORT_TABLES}
            out["meta"] = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM recon_meta")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise MigrationInputError(f"database has no reconciliation data ({type(exc).__name__})") from exc
    return out
