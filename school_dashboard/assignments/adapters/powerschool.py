"""PowerSchool (`powerschool summary --json`) payload -> SourceRecords."""
from __future__ import annotations

from typing import Any, Callable

from school_dashboard.powerschool import first_name

from ..model import SourceRecord
from ..stages import _real_score
from .base import (AdapterResult, counted, empty_result, hash_key, parse_due, resolve_name,
                   text)

_LISTS = (("missing", "missing"), ("late", "late"), ("upcoming", "open"))


def _items_problem(entry: dict[str, Any]) -> str | None:
    """Mirrors state._ps_item_problem plus blank titles; lists must already be lists."""
    for name, _ in _LISTS:
        for item in entry[name]:
            if not isinstance(item, dict) or not text(item.get("title")) or "due" not in item:
                return "item without a title or due field"
    return None


def _entry_problem(entry: dict[str, Any]) -> str | None:
    if not all(isinstance(entry.get(name), list) for name, _ in _LISTS):
        return "lists missing or not lists"
    return _items_problem(entry)


def _record(kid: str, item: dict[str, Any], list_name: str, status: str,
            counts: dict[str, int]) -> SourceRecord:
    title = text(item.get("title"))
    course = text(item.get("course")) or None
    due, bad_due = parse_due(item.get("due"))
    counts["unparseable due dates"] += bad_due
    raw_score = item.get("score")
    score = str(raw_score) if raw_score is not None else None
    if _real_score(score):
        status = "graded"
    return SourceRecord(
        source="powerschool", kid=kid, class_name=course, title=title, due_date=due,
        portal_status=status, raw_status=list_name, score=score, link=None,
        external_key=hash_key(course, title, due), raw=dict(item))


class PowerSchoolAdapter:
    source = "powerschool"

    def parse(self, payload: Any, *, resolve_kid: Callable[[str], str],
              file_ok: bool = True) -> AdapterResult:
        entries = payload.get("children") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return empty_result(self.source, "payload is not a PowerSchool summary")
        clean = file_ok and not payload.get("warnings")
        records: list[SourceRecord] = []
        seen: set[str] = set()
        untrusted: set[str] = set()
        counts = {"child entries without a usable name": 0, "child entries malformed": 0,
                  "child entries with scraper warnings": 0, "unparseable due dates": 0}
        if not clean:
            counts["scrapes not usable for absence (stale file or top-level warnings)"] = 1
        for entry in entries:
            kid = resolve_name(resolve_kid, first_name(entry.get("name") if isinstance(entry, dict) else None))
            if not kid:
                counts["child entries without a usable name"] += 1
                continue
            seen.add(kid)
            if not clean:
                untrusted.add(kid)
            if _entry_problem(entry):
                counts["child entries malformed"] += 1
                untrusted.add(kid)
                continue
            if entry.get("warnings"):
                counts["child entries with scraper warnings"] += 1
                untrusted.add(kid)
            for name, status in _LISTS:
                records.extend(_record(kid, item, name, status, counts) for item in entry[name])
        return AdapterResult(self.source, tuple(records), frozenset(seen),
                             frozenset(seen - untrusted), counted(self.source, counts))
