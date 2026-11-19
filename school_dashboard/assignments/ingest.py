"""Shadow ingest of scraped assignments into the recon tables (S3b).

`run` is the engine (one transaction per kid), `recompute` re-derives flags only, `run_shadow` is the
single entry point `school-state update` calls: flag-guarded, never raises, never prints.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from school_dashboard import paths, sync_meta, watchdog
from school_dashboard.state import canonical_name, get_children

from . import flagsenv, ingest_io, notify, repo, schema, schooldays
from .adapters import AdapterResult
from .health import INGEST_SOURCE
from .ingest_kid import recompute_kid, run_kid
from .ingest_types import IngestReport, KidResult
from .model import SourceRecord
from .repo import NotMigratedError

__all__ = ["IngestReport", "KidResult", "NotMigratedError", "recompute", "run", "run_shadow"]

logger = logging.getLogger(__name__)

INGEST_FLAG = "RECON_INGEST_ENABLED"
NOT_MIGRATED = "legacy items are not migrated; run: school-state assignments migrate"
MAX_RECORDS_PER_KID = 2000
AfterIngest = Callable[[sqlite3.Connection, datetime], object]


def _open(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise NotMigratedError(NOT_MIGRATED)
    conn = schema.connect(db_path)
    try:
        repo.require_migrated(conn)
    except sqlite3.OperationalError as exc:  # recon tables are not there at all
        conn.close()
        raise NotMigratedError(NOT_MIGRATED) from exc
    except NotMigratedError:
        conn.close()
        raise
    try:
        schema.ensure_schema(conn)  # a database migrated under schema v1 is upgraded in place
    except BaseException:
        conn.close()
        raise
    return conn


def _plan(results: Sequence[AdapterResult], roster: frozenset[str] | None = None
          ) -> tuple[dict[str, list[SourceRecord]], dict[str, set[str]], list[str]]:
    """Per kid: the trusted records (in result order) and the sources that trusted the kid.

    Names off a non-empty roster are dropped entirely (one counts-only warning); a kid with more than
    MAX_RECORDS_PER_KID records from one source is untrusted for that source in this run (inert)."""
    records: dict[str, list[SourceRecord]] = defaultdict(list)
    sources: dict[str, set[str]] = defaultdict(set)
    warnings: list[str] = []
    off_names: set[str] = set()
    off_records = 0
    for res in results:
        warnings.extend(res.warnings)
        known = lambda kid: roster is None or kid in roster  # noqa: E731
        off_names |= {k for k in (*res.seen_kids, *res.trusted_kids, *(r.kid for r in res.records)) if not known(k)}
        off_records += sum(1 for rec in res.records if not known(rec.kid))
        per_kid = Counter(rec.kid for rec in res.records)
        capped = {k for k, n in per_kid.items() if n > MAX_RECORDS_PER_KID and known(k)}
        trusted = {k for k in res.trusted_kids if known(k) and k not in capped}
        ignored = 0
        for rec in res.records:
            if not known(rec.kid):
                continue
            if rec.kid in trusted:
                records[rec.kid].append(rec)
            else:
                ignored += 1
        for kid in trusted:
            sources[kid].add(res.source)
        if capped:
            warnings.append(f"{res.source}: {len(capped)} child entries over the record cap ignored")
        if ignored:
            warnings.append(f"{res.source}: {ignored} records from untrusted entries ignored")
    if off_names:
        warnings.append(f"{off_records} records for {len(off_names)} names not on the roster were ignored")
    return records, sources, warnings


def _run_kid(conn: sqlite3.Connection, kid: str, records: Sequence[SourceRecord], trusted: frozenset[str],
             **kw: Any) -> tuple[KidResult, tuple[str, ...]]:
    try:
        with repo.transaction(conn):
            return run_kid(conn, kid, records, trusted, **kw)
    except Exception as exc:  # one kid's failure is rolled back and reported; the others still run
        logger.error("recon ingest: a kid failed (%s)", type(exc).__name__)
        return KidResult(kid=kid, ok=False, error=type(exc).__name__), ()


def _set_baseline(conn: sqlite3.Connection, now: datetime) -> None:
    with repo.transaction(conn):
        repo.set_meta(conn, "baseline_at", now.isoformat())


def run(db_path: str, results: Sequence[AdapterResult], *, file_hashes: Mapping[str, str], now: datetime,
        no_school_rows: Sequence[Mapping[str, Any]], roster: frozenset[str] | None = None) -> IngestReport:
    conn = _open(db_path)
    try:
        baseline = repo.get_meta(conn, "baseline_at") is None
        records, sources, warnings = _plan(results, roster)
        outcomes = [_run_kid(conn, kid, records.get(kid, []), frozenset(sources[kid]), now=now,
                             no_school_rows=no_school_rows, baseline=baseline, file_hashes=file_hashes)
                    for kid in sorted(sources)]
        kids = tuple(o[0] for o in outcomes)
        ok = all(k.ok for k in kids)
        if ok and kids and baseline:
            _set_baseline(conn, now)
    finally:
        conn.close()
    logger.info("recon ingest: %d kids, %d failed, %d created, %d linked, %d merged, %d flag changes",
                len(kids), sum(not k.ok for k in kids), sum(k.created for k in kids),
                sum(k.linked for k in kids), sum(k.merged for k in kids), sum(k.flag_changes for k in kids))
    all_warnings = (*warnings, *(w for o in outcomes for w in o[1]))
    return IngestReport(ok=ok, kids=kids, warnings=all_warnings, baseline=baseline, data_as_of=now.isoformat())


def recompute(conn: sqlite3.Connection, kid: str, *, now: datetime,
              no_school_rows: Sequence[Mapping[str, Any]]) -> KidResult:
    """Flags only (no scrape): the clock moved. Transitions are stored, logged and may enqueue a push."""
    try:
        with repo.transaction(conn):
            baseline = repo.get_meta(conn, "baseline_at") is None
            return recompute_kid(conn, kid, now=now, no_school_rows=no_school_rows, baseline=baseline)
    except Exception as exc:
        logger.error("recon recompute failed (%s)", type(exc).__name__)
        return KidResult(kid=kid, ok=False, error=type(exc).__name__)


# ---- shadow entry point ---------------------------------------------------

def _record(result: str) -> None:
    try:
        sync_meta.write_sync_source(INGEST_SOURCE, result)
    except Exception as exc:
        logger.error("recon ingest: could not record %s (%s)", result, type(exc).__name__)


def _roster() -> frozenset[str] | None:
    """Canonical roster names; None (no filtering) when no roster is configured."""
    return frozenset(canonical_name(n) for n in get_children()) or None


def _ingest(args: Any, now: datetime) -> IngestReport:
    db_path = str(paths.db_path())
    roster = _roster()
    results, hashes, warnings = ingest_io.load_results(args, canonical_name, now)
    rows = ingest_io.read_events(db_path)
    report = run(db_path, results, file_hashes=hashes, now=now, no_school_rows=rows, roster=roster)
    extra = [*warnings]
    if not schooldays.calendar_covers(rows, schooldays.school_today(now)):
        extra.append("school calendar does not cover today")
    if report.ok and report.kids:
        ingest_io.mark_ingest_ok(db_path, now)
    return replace(report, warnings=(*report.warnings, *extra))


def _send_pending(conn: sqlite3.Connection, now: datetime) -> object:
    """Default after-ingest step: the outbox sender (a no-op unless RECON_PUSH_ENABLED, baseline and no maintenance)."""
    return notify.send_pending(conn, now=now)


def _after(db_path: str, now: datetime, after_ingest: AfterIngest) -> None:
    try:
        conn = schema.connect(db_path)
        try:
            after_ingest(conn, now)
        finally:
            conn.close()
    except Exception as exc:  # a sender problem is not an ingest problem
        logger.error("recon after-ingest step failed (%s)", type(exc).__name__)


def run_shadow(args: Any, *, now: datetime | None = None,
               after_ingest: AfterIngest | None = None) -> IngestReport | None:
    """Called once at the end of `school-state update`; None when the flag is off or the ingest failed."""
    if not flagsenv.enabled(INGEST_FLAG):
        return None
    try:
        when = now or datetime.now(watchdog.get_tz())
        report = _ingest(args, when)
    except Exception as exc:
        if isinstance(exc, NotMigratedError):
            logger.error("recon ingest refused: %s", exc)
        else:
            logger.error("recon ingest failed (%s)", type(exc).__name__)
        _record("error")
        return None
    if not report.kids:  # nothing was ingested: stay silent so the watchdog's stale rule takes over
        logger.info("recon ingest: nothing ingested (no trusted entry from any source), %d warnings",
                    len(report.warnings))
    else:
        _record("ok" if report.ok else "error")
        guards = sum("mass change guard" in w for w in report.warnings)
        logger.info("recon ingest: %d warnings", len(report.warnings))
        if guards:
            logger.warning("recon ingest: mass change guard held back absence for %d source entries", guards)
    if report.ok:
        _after(str(paths.db_path()), when, after_ingest or _send_pending)
    return report
