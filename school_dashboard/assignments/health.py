"""Ingest freshness for the read-time `unverified` overlay (pure; delegates to the watchdog)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from types import MappingProxyType
from typing import Any, Mapping

from school_dashboard.sync_meta import last_ok_time
from school_dashboard.watchdog import check_source, parse_ts

INGEST_SOURCE = "recon_ingest"
SYNC_NAME = {"schoology": "sgy", "powerschool": "powerschool", "ixl": "ixl"}  # link source -> sync_meta key
_NO_FIRST_SEEN: Mapping[str, datetime] = MappingProxyType({})


@dataclass(frozen=True)
class IngestHealth:
    ok: bool
    data_as_of: datetime | None
    issue: str | None


def _entry(meta: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    entry = meta.get(name)
    return entry if isinstance(entry, dict) else None


def ingest_health(meta: Mapping[str, Any], *, now: datetime, tz: tzinfo,
                  first_seen: datetime | None = None) -> IngestHealth:
    entry = _entry(meta, INGEST_SOURCE)
    as_of = parse_ts(last_ok_time(entry))
    problem = check_source(INGEST_SOURCE, entry, now, tz, first_seen=first_seen)
    if entry is None:
        return IngestHealth(False, None, problem.issue if problem else "missing")
    if entry.get("last_result") == "error":
        return IngestHealth(False, as_of, "error")
    if problem is not None:
        return IngestHealth(False, as_of, problem.issue)
    if as_of is None:
        return IngestHealth(False, None, "never_ok")
    return IngestHealth(True, as_of, None)


def stale_sources(meta: Mapping[str, Any], *, now: datetime, tz: tzinfo,
                  first_seen: Mapping[str, datetime] = _NO_FIRST_SEEN) -> frozenset[str]:
    """Link-source names whose sync is overdue by the watchdog rule (first_seen keyed by sync name)."""
    return frozenset(
        link for link, sync in SYNC_NAME.items()
        if check_source(sync, _entry(meta, sync), now, tz, first_seen=first_seen.get(sync)) is not None)
