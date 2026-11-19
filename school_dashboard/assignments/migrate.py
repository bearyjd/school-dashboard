"""Migration of legacy `items` and state `action_items` into reconciliation assignments (plan 3.7, 10.8)."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from school_dashboard import maintenance, paths, state as state_mod
from school_dashboard.assignments import migrate_backup, repo, schema
from school_dashboard.assignments.migrate_plan import MigrationPlan, PlannedRecord, plan_migration
from school_dashboard.assignments.model import LinkState

logger = logging.getLogger(__name__)

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


class MigrationRefused(RuntimeError):
    """Maintenance mode is off or a sync is in flight."""


class MigrationError(RuntimeError):
    """Verification mismatch, backup failure or a failed write (everything rolled back)."""


class MigrationInputError(ValueError):
    """The database or state file is missing or malformed."""


def _items_sha(conn: sqlite3.Connection) -> str:
    rows = [list(r) for r in conn.execute("SELECT * FROM items ORDER BY id").fetchall()]
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (name,)).fetchone() is not None


def _load_action_items(state_path: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(Path(state_path).read_text())
    except (OSError, ValueError) as exc:
        raise MigrationInputError(f"state file unreadable ({type(exc).__name__})") from exc
    actions = data.get("action_items", []) if isinstance(data, dict) else None
    if not isinstance(actions, list) or not all(isinstance(a, dict) and "id" in a for a in actions):
        raise MigrationInputError("state file has no valid action_items list")
    return actions


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
        items = [dict(r) for r in conn.execute("SELECT * FROM items ORDER BY id").fetchall()]
        sha = _items_sha(conn)
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
    off = [k for k in per_kid if k not in roster]
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


def _verify(conn: sqlite3.Connection, plan: MigrationPlan, before: tuple[int, int], expected_keys: set[str],
            sha_before: str) -> str:
    after = (_count(conn, "assignments"), _count(conn, "source_links"))
    created, merged = len(plan.records), len(plan.merged)
    if after[0] - before[0] != created or after[1] - before[1] != created + merged:
        raise MigrationError("row counts in the database do not match the plan")
    present = {r[0] for r in conn.execute("SELECT external_key FROM source_links")}
    if expected_keys - present:
        raise MigrationError("some legacy rows have no assignment link in the database")
    sha_after = _items_sha(conn)
    if sha_after != sha_before:
        raise MigrationError("items table changed during migration")
    return sha_after


def _apply(db_path: str, plan: MigrationPlan, expected_keys: set[str], sha_before: str,
           now: datetime) -> str:
    conn = schema.connect(db_path)
    try:
        with repo.transaction(conn):
            schema.ensure_schema(conn)
            before = (_count(conn, "assignments"), _count(conn, "source_links"))
            if _items_sha(conn) != sha_before:
                raise MigrationError("items table changed since the plan was made")
            for rec in plan.records:
                _write_record(conn, rec, now)
            sha_after = _verify(conn, plan, before, expected_keys, sha_before)
            repo.set_meta(conn, "migrated_at", now.isoformat())
        return sha_after
    except (sqlite3.Error, repo.RepoError, repo.BusyError) as exc:
        raise MigrationError(f"migration rolled back ({type(exc).__name__})") from exc
    finally:
        conn.close()


def _preflight() -> None:
    if not maintenance.is_active():
        raise MigrationRefused("maintenance mode is not active; run: school-state transition maintenance on")
    if maintenance.sync_in_flight():
        raise MigrationRefused("a sync appears to be running; wait for it to finish and retry")


def run(db_path: str, state_path: str, *, dry_run: bool, now: datetime,
        archive_dir: str | None = None) -> MigrationReport:
    if not dry_run:
        _preflight()
    actions = _load_action_items(state_path)
    snap = _snapshot(db_path)
    todo_items = [i for i in snap.items if i["id"] not in snap.item_ids_done]
    todo_actions = [a for a in actions if str(a["id"]) not in snap.action_ids_done]
    plan = plan_migration(todo_items, todo_actions, now=now, resolve_kid=state_mod.canonical_name)
    rows_in = len(snap.items) + len(actions)
    already = rows_in - plan.rows_in
    warnings = _roster_warnings(plan.per_kid)
    backup_dir, sha_after = None, snap.sha
    if not dry_run and (plan.records or not snap.migrated):
        root = Path(archive_dir) if archive_dir else paths.state_dir() / "archive"
        try:
            backup_dir = migrate_backup.make_backup(db_path, state_path, root, now)
        except migrate_backup.BackupError as exc:
            raise MigrationError(str(exc)) from exc
        keys = {f"item:{i['id']}" for i in snap.items} | {f"legacy-action:{a['id']}" for a in actions}
        sha_after = _apply(db_path, plan, keys, snap.sha, now)
    logger.info("migration %s: rows_in=%d created=%d merged=%d already=%d", "dry-run" if dry_run else "done",
                rows_in, len(plan.records), len(plan.merged), already)
    return MigrationReport(dry_run, rows_in, len(plan.records), plan.merged, plan.per_kid, warnings,
                           backup_dir, snap.sha, sha_after, already)


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
