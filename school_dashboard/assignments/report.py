"""Merge-quality report: pure builders over plain rows, plus a thin read-only reader.

The report holds titles and class names by design (it is the operator's tool); `report_render.redact`
swaps them for stable hashes when it has to be shared.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any, Iterator, Mapping, Sequence

from school_dashboard import sync_meta, watchdog

from . import repo, schema, schooldays
from .dates import parse_date
from .health import IngestHealth, ingest_health
from .report_render import redact, render_text

__all__ = ["AssignmentRow", "LinkRow", "ReportInput", "alias_suggestions", "build_report", "read_input",
           "redact", "render_text", "run", "unmatched_pairs"]

SIMILAR_TITLE = 0.6
MAX_PAIRS = 500
TITLE_COMPARE_CHARS = 120
_EVIDENCE_STATUSES = ("missing", "late")


@dataclass(frozen=True)
class LinkRow:
    source: str
    norm_status: str
    live: bool


@dataclass(frozen=True)
class AssignmentRow:
    id: int
    kid: str
    class_name: str | None
    class_key: str
    title: str
    title_key: str
    due_date: date | None
    stage: int
    flag: str | None
    links: tuple[LinkRow, ...]

    @property
    def sources(self) -> frozenset[str]:
        return frozenset(ln.source for ln in self.links)


@dataclass(frozen=True)
class ReportInput:
    assignments: tuple[AssignmentRow, ...]
    duplicates: Mapping[str, int]  # kid -> assignments created as possible_duplicate
    outbox: Mapping[str, int]  # status -> count
    baseline_at: str | None = None
    debounced: int = 0  # pushes skipped by the debounce in the last 24h (counts only)
    schema_version: int = 1
    legacy_rows: int = 0
    bad_calendar: int = 0  # calendar rows with an unreadable date
    bad_due: int = 0  # assignment rows with an unreadable due date


# ---- reader ---------------------------------------------------------------

def read_input(conn: sqlite3.Connection, now: datetime | None = None) -> ReportInput:
    version = schema.user_version(conn)
    live = " WHERE merged_into IS NULL" if version >= 2 else ""
    live_join = " AND a.merged_into IS NULL" if version >= 2 else ""
    links: dict[int, list[LinkRow]] = defaultdict(list)
    for r in conn.execute("SELECT assignment_id, source, norm_status, live FROM source_links ORDER BY id"):
        links[r["assignment_id"]].append(LinkRow(r["source"], r["norm_status"], bool(r["live"])))
    stored = conn.execute(f"SELECT * FROM assignments{live} ORDER BY id").fetchall()
    rows = tuple(
        AssignmentRow(id=r["id"], kid=r["kid"], class_name=r["class_name"], class_key=r["class_key"] or "",
                      title=r["title"], title_key=r["title_key"], due_date=parse_date(r["due_date"]),
                      stage=r["stage"], flag=r["flag"], links=tuple(links.get(r["id"], ())))
        for r in stored)
    bad_due = sum(bool(r["due_date"]) and parse_date(r["due_date"]) is None for r in stored)
    calendar = repo.calendar_rows(conn)
    dups = {r["kid"]: r["n"] for r in conn.execute(
        "SELECT a.kid, COUNT(*) AS n FROM assignment_events e JOIN assignments a ON a.id = e.assignment_id"
        f" WHERE e.kind = 'merge' AND e.detail LIKE 'possible_duplicate%'{live_join} GROUP BY a.kid")}
    outbox = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM push_outbox WHERE COALESCE(last_error, '') != ? GROUP BY status",
        (repo.DEBOUNCED,))}
    since = (now - timedelta(hours=24)).isoformat() if now else "0001-01-01T00:00:00"
    debounced = conn.execute(
        "SELECT COUNT(*) FROM push_outbox WHERE last_error = ? AND julianday(created_at) > julianday(?)",
        (repo.DEBOUNCED, since)).fetchone()[0]
    legacy = schema.legacy_violations(conn) if version >= 2 else 0
    return ReportInput(rows, dups, outbox, repo.get_meta(conn, "baseline_at"), schema_version=version,
                       legacy_rows=legacy, debounced=debounced, bad_calendar=schooldays.bad_calendar_rows(calendar),
                       bad_due=bad_due)


# ---- pure builders ---------------------------------------------------------

def _covered(row: AssignmentRow) -> bool:
    """Could any link give stage-4 evidence: a schoology link, or a powerschool link currently missing/late."""
    return any(ln.source == "schoology" or (ln.source == "powerschool" and ln.live
                                            and ln.norm_status in _EVIDENCE_STATUSES) for ln in row.links)


def _kid_stats(rows: Sequence[AssignmentRow], duplicates: int) -> dict[str, Any]:
    per_source: dict[str, dict[str, int]] = {}
    for row in rows:
        for ln in row.links:
            stat = per_source.setdefault(ln.source, {"links": 0, "live": 0})
            stat["links"] += 1
            stat["live"] += int(ln.live)
    multi = sum(1 for r in rows if len(r.sources) >= 2)
    open_rows = [r for r in rows if r.stage < 4]
    return {
        "sources": per_source, "assignments": len(rows), "multi_source": multi,
        "merge_rate": round(multi / len(rows), 3) if rows else 0.0, "possible_duplicates": duplicates,
        "flags": dict(sorted(Counter(r.flag for r in rows if r.flag).items())),
        "stages": {str(k): v for k, v in sorted(Counter(r.stage for r in rows).items())},
        "evidence_coverage": round(sum(_covered(r) for r in open_rows) / len(open_rows), 3) if open_rows else None,
    }


def _pair(a: AssignmentRow, b: AssignmentRow) -> dict[str, Any] | None:
    if not a.sources or not b.sources or a.sources & b.sources or a.due_date is None or a.due_date != b.due_date:
        return None
    ratio = SequenceMatcher(None, a.title_key[:TITLE_COMPARE_CHARS], b.title_key[:TITLE_COMPARE_CHARS]).ratio()
    same_title = a.title_key == b.title_key
    classes_differ = bool(a.class_key and b.class_key and a.class_key != b.class_key)
    if not ((same_title and classes_differ) or ratio >= SIMILAR_TITLE):
        return None
    alias = {"from": a.class_name, "to": b.class_name} if same_title and classes_differ else None
    return {"kid": a.kid, "title_a": a.title, "title_b": b.title, "class_a": a.class_name,
            "class_b": b.class_name, "due": a.due_date.isoformat(), "ratio": round(ratio, 3),
            "sources_a": sorted(a.sources), "sources_b": sorted(b.sources), "suggested_alias": alias}


def _candidates(rows: Sequence[AssignmentRow]) -> Iterator[tuple[AssignmentRow, AssignmentRow]]:
    """Pairs that could be the same assignment: same kid and due date (bucketed, never all-pairs)."""
    buckets: dict[tuple[str, date], list[AssignmentRow]] = defaultdict(list)
    for row in rows:
        if row.due_date is not None and row.sources:
            buckets[(row.kid, row.due_date)].append(row)
    for key in sorted(buckets):
        yield from combinations(buckets[key], 2)


def _find_pairs(rows: Sequence[AssignmentRow], limit: int = MAX_PAIRS) -> tuple[list[dict[str, Any]], bool]:
    """(at most `limit` pairs, whether more existed)."""
    out: list[dict[str, Any]] = []
    for a, b in _candidates(rows):
        pair = _pair(a, b)
        if pair is None:
            continue
        if len(out) >= limit:
            return out, True
        out.append(pair)
    return out, False


def unmatched_pairs(rows: Sequence[AssignmentRow], limit: int = MAX_PAIRS) -> list[dict[str, Any]]:
    return _find_pairs(rows, limit)[0]


def alias_suggestions(pairs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    for p in pairs:
        if p["suggested_alias"]:
            first, second = sorted((p["class_a"], p["class_b"]), key=lambda c: (c.casefold(), c))
            counts[(p["kid"], first, second)] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"kid": k, "class_a": a, "class_b": b, "count": n} for (k, a, b), n in ranked]


def _warnings(inp: ReportInput) -> list[str]:
    notes = [schooldays.calendar_warning(inp.bad_calendar),
             f"{inp.bad_due} assignment rows had unreadable dates" if inp.bad_due else None]
    return [n for n in notes if n]


def build_report(inp: ReportInput, health: IngestHealth) -> dict[str, Any]:
    by_kid: dict[str, list[AssignmentRow]] = defaultdict(list)
    for row in inp.assignments:
        by_kid[row.kid].append(row)
    pairs, truncated = _find_pairs(inp.assignments)
    return {
        "kids": {kid: _kid_stats(rows, inp.duplicates.get(kid, 0)) for kid, rows in sorted(by_kid.items())},
        "unmatched_pairs": pairs, "unmatched_truncated": truncated, "baseline_at": inp.baseline_at,
        "alias_suggestions": alias_suggestions(pairs),
        "health": {"ok": health.ok, "data_as_of": health.data_as_of.isoformat() if health.data_as_of else None,
                   "issue": health.issue},
        "outbox": dict(sorted(inp.outbox.items())), "debounced_last_24h": inp.debounced,
        "schema": {"version": inp.schema_version, "legacy_rows_outside_limits": inp.legacy_rows},
        "warnings": _warnings(inp),
    }


def run(db_path: str, *, now: datetime, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Read-only report from the database; NotMigratedError when the recon tables are not there."""
    try:
        conn = schema.connect(db_path, readonly=True)
        try:
            repo.require_migrated(conn)
            inp = read_input(conn, now)
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        raise repo.NotMigratedError("legacy items are not migrated; run: school-state assignments migrate") from exc
    schooldays.log_bad_calendar(inp.bad_calendar)
    health = ingest_health(sync_meta.read_sync_meta() if meta is None else meta, now=now, tz=watchdog.get_tz())
    return build_report(inp, health)
