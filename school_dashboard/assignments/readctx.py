"""Read context shared by /api/v2 and the digest cron: database connection, roster, calendar, sync freshness (no Flask)."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterator, Mapping

from school_dashboard import paths, watchdog
from school_dashboard.assignments import health, repo, schema
from school_dashboard.assignments.health import IngestHealth
from school_dashboard.assignments.schooldays import no_school_dates
from school_dashboard.state import get_roster
from school_dashboard.sync_meta import read_sync_meta

logger = logging.getLogger(__name__)
_NO_SYNC_META: Mapping[str, Any] = {}


@dataclass(frozen=True)
class ReadCtx:
    now: datetime
    roster: tuple[str, ...]
    kids: frozenset[str]
    events: tuple[Mapping[str, Any], ...]
    no_school_by_kid: Mapping[str, frozenset[date]]
    grace_by_class: Mapping[tuple[str, str], int]
    stale_sources: frozenset[str]
    ingest: IngestHealth

    def view(self) -> dict[str, Any]:
        """Keyword arguments shared by the read-model builders."""
        return {"now": self.now, "no_school_by_kid": self.no_school_by_kid, "grace_by_class": self.grace_by_class,
                "stale_sources": self.stale_sources, "ingest": self.ingest}


def roster_names() -> tuple[str, ...]:
    return tuple(str(c["name"]) for c in get_roster())


def db_file() -> str:
    return str(paths.db_path())


def is_migrated() -> bool:
    try:
        open_db(readonly=True).close()
    except repo.NotMigratedError:
        return False
    return True


def open_db(*, readonly: bool) -> sqlite3.Connection:
    """A new connection to a migrated v2 database; NotMigratedError when the file or the tables are not there."""
    path = db_file()
    if not os.path.exists(path):
        raise repo.NotMigratedError("no database")
    conn = schema.connect(path, readonly=readonly)
    try:
        repo.require_migrated(conn)
        if schema.user_version(conn) < schema.SCHEMA_VERSION:
            raise repo.NotMigratedError("schema is not upgraded")
    except sqlite3.OperationalError as exc:
        conn.close()
        raise repo.NotMigratedError("recon tables missing") from exc
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def connection(*, readonly: bool) -> Iterator[sqlite3.Connection]:
    conn = open_db(readonly=readonly)
    try:
        yield conn
    finally:
        conn.close()


def _sync_meta() -> Mapping[str, Any]:
    meta = read_sync_meta(str(paths.sync_meta_path()))
    return meta if isinstance(meta, dict) else _NO_SYNC_META


def _first_seen() -> Mapping[str, datetime]:
    try:
        return watchdog.load_first_seen()
    except Exception as exc:  # a damaged watchdog file only loses the grace period
        logger.warning("api v2: first_seen unavailable (%s)", type(exc).__name__)
        return {}


def load(conn: sqlite3.Connection, now: datetime) -> ReadCtx:
    roster = roster_names()
    kids = frozenset(roster) if roster else frozenset(repo.assignment_kids(conn))
    events = tuple(repo.calendar_rows(conn))
    everyone = {*roster, *repo.assignment_kids(conn)}
    tz, meta, seen = watchdog.get_tz(), _sync_meta(), _first_seen()
    return ReadCtx(
        now=now, roster=roster, kids=kids, events=events,
        no_school_by_kid={k: no_school_dates(events, k) for k in everyone}, grace_by_class=repo.class_grace(conn),
        stale_sources=health.stale_sources(meta, now=now, tz=tz, first_seen=seen),
        ingest=health.ingest_health(meta, now=now, tz=tz, first_seen=seen.get("recon_ingest")))


def alias_list(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [a for a in data if isinstance(a, str)] if isinstance(data, list) else []
