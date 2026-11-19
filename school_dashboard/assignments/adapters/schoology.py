"""Schoology (`sgy summary --json`) payload -> SourceRecords."""
from __future__ import annotations

from typing import Any, Callable

from ..model import SourceRecord
from .base import (AdapterResult, clean_text, counted, empty_result, hash_key, http_link, parse_due,
                   resolve_name, text)

_STATUS = {
    "submitted": "submitted", "turned in": "submitted", "complete": "submitted",
    "completed": "submitted", "graded": "graded", "missing": "missing", "late": "late",
    "unknown": "open", "": "open",
}
_UNTRUSTED_CONFIDENCE = ("partial", "failed")


def entry_problem(entry: dict[str, Any]) -> str | None:
    """Same rule as state.sgy_assignments_problem: every row needs a str title and a due_date key."""
    rows = entry.get("assignments", [])
    if not isinstance(rows, list):
        return "assignments is not a list"
    for row in rows:
        if not isinstance(row, dict):
            return "assignment is not an object"
        if not isinstance(row.get("title"), str):
            return "assignment without a title"
        if "due_date" not in row:
            return "assignment without a due_date"
    return None


def _kid_name(entry: Any) -> str:
    info = entry.get("child") if isinstance(entry, dict) else None
    parts = text(info.get("name") if isinstance(info, dict) else None).split()
    return parts[0] if parts else ""


def _confidence_ok(entry: dict[str, Any]) -> bool:
    if "scrape_confidence" not in entry:
        return True
    conf = entry["scrape_confidence"]
    return isinstance(conf, str) and conf.strip().casefold() not in _UNTRUSTED_CONFIDENCE


def _external_key(raw_link: str, link: str | None, fallback: str) -> str:
    if link:
        return link
    if raw_link.startswith("/") and len(raw_link) > 1:  # relative portal path: still a stable id
        return raw_link
    return fallback


def _record(kid: str, row: dict[str, Any], counts: dict[str, int]) -> SourceRecord | None:
    title = clean_text(row.get("title"), 300)
    if not title:
        counts["assignments without a title skipped"] += 1
        return None
    course = clean_text(row.get("course"), 120) or None
    due, bad_due = parse_due(row.get("due_date"))
    counts["unparseable due dates"] += bad_due
    status = row.get("status")
    mapped = _STATUS.get(text(status).casefold()) if status is None or isinstance(status, str) else None
    if mapped is None:
        counts["assignments with unrecognised status"] += 1
    raw_link = text(row.get("link"))
    link = http_link(raw_link)
    return SourceRecord(
        source="schoology", kid=kid, class_name=course, title=title, due_date=due,
        portal_status=mapped or "open", raw_status=clean_text(status, 80),
        score=None, link=link,
        external_key=_external_key(raw_link, link, hash_key(course, title, due)),
        raw=dict(row))


class SchoologyAdapter:
    source = "schoology"

    def parse(self, payload: Any, *, resolve_kid: Callable[[str], str]) -> AdapterResult:
        entries = payload.get("per_child") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return empty_result(self.source, "payload is not a Schoology summary")
        top_clean = not payload.get("warnings")
        records: list[SourceRecord] = []
        seen: set[str] = set()
        untrusted: set[str] = set()
        counts = {"child entries without a usable name": 0, "child entries with malformed assignments": 0,
                  "child entries partial or failed": 0, "assignments without a title skipped": 0,
                  "unparseable due dates": 0, "assignments with unrecognised status": 0}
        if not top_clean:
            counts["top-level scraper warnings"] = 1
        for entry in entries:
            kid = resolve_name(resolve_kid, _kid_name(entry))
            if not kid:
                counts["child entries without a usable name"] += 1
                continue
            seen.add(kid)
            if entry_problem(entry):
                counts["child entries with malformed assignments"] += 1
                untrusted.add(kid)
                continue
            if not _confidence_ok(entry):
                counts["child entries partial or failed"] += 1
                untrusted.add(kid)
            if not top_clean:
                untrusted.add(kid)
            built = (_record(kid, row, counts) for row in entry.get("assignments", []))
            records.extend(rec for rec in built if rec)
        return AdapterResult(self.source, tuple(records), frozenset(seen),
                             frozenset(seen - untrusted),
                             counted(self.source, counts))
