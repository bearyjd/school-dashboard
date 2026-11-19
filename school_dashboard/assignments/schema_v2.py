"""Schema v2: validation and append-only triggers (additive; SQLite cannot add CHECKs without a table rebuild).

Every rule is one SQL condition over a row reference `{r}` (`NEW` in a trigger, the table name when counting
legacy rows). Update triggers fire only when the column actually changes, so a legacy value outside the new
limits never blocks an unrelated update.
"""
from __future__ import annotations

import logging
import re
import sqlite3

from .model import EVENT_KINDS, FLAGS, KINDS, LINK_VIAS, PORTAL_STATUSES, PUSH_STATUSES, SETTERS

logger = logging.getLogger(__name__)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{{r}}.{col} IN ({', '.join(repr(v) for v in values)})"


def _len(col: str, low: int, high: int) -> str:
    return f"length({{r}}.{col}) BETWEEN {low} AND {high}"


def _opt_len(col: str, high: int) -> str:
    return f"{{r}}.{col} IS NULL OR length({{r}}.{col}) <= {high}"


# (table, column, condition)
RULES: tuple[tuple[str, str, str], ...] = (
    ("assignments", "kind", _in("kind", KINDS)),
    ("assignments", "stage", "{r}.stage BETWEEN 1 AND 4"),
    ("assignments", "portal_status", _in("portal_status", PORTAL_STATUSES)),
    ("assignments", "flag", f"{{r}}.flag IS NULL OR {_in('flag', FLAGS)}"),
    ("assignments", "kid", _len("kid", 1, 80)),
    ("assignments", "title", _len("title", 1, 300)),
    ("assignments", "class_name", _opt_len("class_name", 120)),
    ("assignments", "notes", _opt_len("notes", 2000)),
    ("source_links", "source", f"{_len('source', 1, 32)} AND {{r}}.source NOT GLOB '*[^a-z0-9_-]*'"),
    ("source_links", "norm_status", _in("norm_status", PORTAL_STATUSES)),
    ("source_links", "via", _in("via", LINK_VIAS)),
    ("source_links", "url", _opt_len("url", 2000)),
    ("source_links", "raw_json", "length({r}.raw_json) <= 16384"),
    ("assignment_events", "setter", _in("setter", SETTERS)),
    ("assignment_events", "kind", _in("kind", EVENT_KINDS)),
    ("assignment_events", "actor", _opt_len("actor", 40)),
    ("assignment_events", "detail", _opt_len("detail", 500)),
    ("push_outbox", "status", _in("status", PUSH_STATUSES)),
    ("push_outbox", "flag", _in("flag", FLAGS)),
    ("class_settings", "grace_school_days", "{r}.grace_school_days IS NULL OR {r}.grace_school_days BETWEEN 0 AND 10"),
    ("class_settings", "paper_only", "{r}.paper_only IS NULL OR {r}.paper_only IN (0, 1)"),
    ("class_settings", "aliases", _opt_len("aliases", 4000)),
    ("class_settings", "teacher_name", _opt_len("teacher_name", 120)),
    ("class_settings", "teacher_email", _opt_len("teacher_email", 200)),
)
_PROTECT = (
    ("assignment_events", "UPDATE"), ("assignment_events", "DELETE"),
    ("assignments", "DELETE"), ("source_links", "DELETE"),
)
_SAFE = re.compile(r"^[a-z_]+$")


def _cond(condition: str, ref: str) -> str:
    return f"COALESCE(({condition.format(r=ref)}), 0)"


def legacy_violations(conn: sqlite3.Connection) -> int:
    """Rows (summed over tables) that break at least one validation rule."""
    total = 0
    for table in sorted({t for t, _, _ in RULES}):
        bad = " OR ".join(f"NOT {_cond(c, table)}" for t, _, c in RULES if t == table)
        total += int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {bad}").fetchone()[0])  # constants only
    return total


def _create_triggers(conn: sqlite3.Connection) -> None:
    for table, col, condition in RULES:
        assert _SAFE.match(table) and _SAFE.match(col)
        code = f"{table}.{col}"
        check = f"SELECT RAISE(ABORT, '{code}') WHERE NOT {_cond(condition, 'NEW')};"
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS trg_{table}_{col}_ins BEFORE INSERT ON {table} BEGIN {check} END")
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS trg_{table}_{col}_upd BEFORE UPDATE OF {col} ON {table} "
                     f"WHEN NEW.{col} IS NOT OLD.{col} BEGIN {check} END")
    for table, verb in _PROTECT:
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_{verb.lower()} BEFORE {verb} ON {table} "
                     f"BEGIN SELECT RAISE(ABORT, '{table}.{verb.lower()}_forbidden'); END")


_AUDIT_TABLE = (
    "CREATE TABLE IF NOT EXISTS class_settings_events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,"
    " kid TEXT NOT NULL, class_key TEXT NOT NULL, setter TEXT NOT NULL, actor TEXT, changes TEXT NOT NULL)")


def ensure_audit(conn: sqlite3.Connection) -> None:
    """The append-only class-settings audit log; idempotent, also for a v2 database created before it existed."""
    conn.execute(_AUDIT_TABLE)
    for verb in ("UPDATE", "DELETE"):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS trg_class_settings_events_no_{verb.lower()} BEFORE {verb} ON "
                     f"class_settings_events BEGIN SELECT RAISE(ABORT, 'class_settings_events.{verb.lower()}_forbidden');"
                     " END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS trg_class_settings_events_changes BEFORE INSERT ON "
                 "class_settings_events WHEN length(NEW.changes) > 2000 BEGIN SELECT RAISE(ABORT, "
                 "'class_settings_events.changes'); END")


def migrate_v2(conn: sqlite3.Connection) -> None:
    """ALTER ADD COLUMN plus triggers only; v1 tables are never rebuilt."""
    conn.execute("ALTER TABLE assignments ADD COLUMN merged_into INTEGER REFERENCES assignments(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assignments_merged ON assignments (merged_into)")
    legacy = legacy_violations(conn)
    _create_triggers(conn)
    ensure_audit(conn)
    logger.info("recon schema v2: %d legacy rows outside the new limits", legacy)
