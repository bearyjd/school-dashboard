"""SQLite connection policy and versioned DDL for the reconciliation tables (additive only)."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable

from .schema_v2 import legacy_violations, migrate_v2

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5000

_V1_DDL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kid TEXT NOT NULL, class_name TEXT, class_key TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL, title_key TEXT NOT NULL, due_date TEXT,
        kind TEXT NOT NULL CHECK (kind IN ('assignment', 'task', 'practice')),
        stage INTEGER NOT NULL DEFAULT 1 CHECK (stage BETWEEN 1 AND 4),
        stage_since TEXT NOT NULL, received_evidence TEXT,
        portal_status TEXT NOT NULL DEFAULT 'none'
            CHECK (portal_status IN ('none', 'open', 'submitted', 'graded', 'missing', 'late', 'cleared')),
        portal_conflict INTEGER NOT NULL DEFAULT 0, score TEXT,
        flag TEXT, flag_since TEXT, unverified INTEGER NOT NULL DEFAULT 0,
        notes TEXT, legacy_item_id INTEGER UNIQUE, legacy_action_id TEXT UNIQUE,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, closed_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_assignments_kid_flag ON assignments (kid, flag)",
    "CREATE INDEX IF NOT EXISTS idx_assignments_key ON assignments (kid, class_key, title_key, due_date)",
    """CREATE TABLE IF NOT EXISTS source_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment_id INTEGER NOT NULL REFERENCES assignments (id),
        source TEXT NOT NULL, kid_external TEXT NOT NULL, external_key TEXT NOT NULL,
        url TEXT, raw_status TEXT, norm_status TEXT NOT NULL, score TEXT, raw_json TEXT NOT NULL,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, absent_since TEXT,
        trusted_misses INTEGER NOT NULL DEFAULT 0, live INTEGER NOT NULL DEFAULT 1,
        last_miss_hash TEXT, via TEXT NOT NULL DEFAULT 'scrape',
        UNIQUE (source, kid_external))""",
    "CREATE INDEX IF NOT EXISTS idx_source_links_assignment ON source_links (assignment_id)",
    """CREATE TABLE IF NOT EXISTS assignment_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment_id INTEGER NOT NULL REFERENCES assignments (id),
        at TEXT NOT NULL, setter TEXT NOT NULL, actor TEXT, kind TEXT NOT NULL,
        source TEXT, from_value TEXT, to_value TEXT, detail TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_assignment_events_assignment ON assignment_events (assignment_id, id)",
    """CREATE TABLE IF NOT EXISTS class_settings (
        kid TEXT NOT NULL, class_key TEXT NOT NULL, grace_school_days INTEGER,
        paper_only INTEGER NOT NULL DEFAULT 0, teacher_name TEXT, teacher_email TEXT, aliases TEXT,
        PRIMARY KEY (kid, class_key))""",
    """CREATE TABLE IF NOT EXISTS note_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment_id INTEGER NOT NULL REFERENCES assignments (id),
        flag TEXT, body TEXT, status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'sent_marked', 'discarded')),
        created_at TEXT NOT NULL, actor TEXT)""",
    """CREATE TABLE IF NOT EXISTS push_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment_id INTEGER NOT NULL REFERENCES assignments (id),
        flag TEXT NOT NULL, occurrence_at TEXT NOT NULL,
        reason TEXT NOT NULL CHECK (reason IN ('transition', 'baseline')),
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'sent', 'failed', 'suppressed')),
        attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, sent_at TEXT, last_error TEXT,
        UNIQUE (assignment_id, flag, occurrence_at))""",
    "CREATE TABLE IF NOT EXISTS recon_meta (key TEXT PRIMARY KEY, value TEXT)",
)


def _migrate_v1(conn: sqlite3.Connection) -> None:
    for statement in _V1_DDL:
        conn.execute(statement)


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _migrate_v1, 2: migrate_v2}
__all__ = ["BUSY_TIMEOUT_MS", "SCHEMA_VERSION", "connect", "ensure_schema", "legacy_violations", "user_version"]


def _is_wal_file(db_path: str) -> bool:
    """True when the database header says WAL mode (file-format version bytes 18-19 are 2)."""
    try:
        with open(db_path, "rb") as fh:
            header = fh.read(20)
    except OSError:
        return False
    return len(header) == 20 and header[18] == 2 and header[19] == 2


def connect(db_path: str, *, readonly: bool = False) -> sqlite3.Connection:
    """Open `db_path` with the shared policy (Row factory, FK on, busy timeout, WAL, autocommit)."""
    if readonly and not _is_wal_file(db_path):
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    elif readonly:
        # A mode=ro handle cannot remove the -wal/-shm files it creates; query_only still forbids writes.
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("PRAGMA query_only=ON")
    else:
        conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if not readonly:
            conn.execute("PRAGMA journal_mode=WAL")
    except BaseException:
        conn.close()
        raise
    return conn


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _apply_pending(conn: sqlite3.Connection, current: int) -> None:
    for version in range(current + 1, SCHEMA_VERSION + 1):
        _MIGRATIONS[version](conn)
        conn.execute(f"PRAGMA user_version={version}")  # constant int from the migration table
        logger.info("recon schema upgraded to version %d", version)


def ensure_schema(conn: sqlite3.Connection) -> int:
    """Bring the reconciliation tables up to SCHEMA_VERSION; idempotent. Joins an open transaction."""
    current = user_version(conn)
    if current >= SCHEMA_VERSION:
        return current
    if conn.in_transaction:
        _apply_pending(conn, current)
        return SCHEMA_VERSION
    conn.execute("BEGIN IMMEDIATE")
    try:
        _apply_pending(conn, user_version(conn))
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return SCHEMA_VERSION
