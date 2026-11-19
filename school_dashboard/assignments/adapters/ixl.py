"""IXL (`ixl assigned --json`, one payload per child) -> one SourceRecord per subject with work."""
from __future__ import annotations

from typing import Any, Callable

from ..model import SourceRecord
from .base import AdapterResult, counted, empty_result, resolve_name, text

_COUNT_KEYS = ("assigned", "done", "remaining")


def _is_count(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def payload_problem(data: Any) -> str | None:
    """Mirrors state.ixl_payload_ok + ixl_totals_problem."""
    if not isinstance(data, dict):
        return "payload is not an object"
    if data.get("status") == "error" or data.get("errors"):
        return "scraper reported an error"
    if "totals" not in data and "remaining" not in data:
        return "no totals"
    totals = data.get("totals")
    if not isinstance(totals, dict):
        return "totals missing"
    for subject in totals.values():
        if not isinstance(subject, dict) or not all(_is_count(subject.get(k)) for k in _COUNT_KEYS):
            return "totals incomplete"
    return None


def _records(kid: str, totals: dict[str, Any]) -> list[SourceRecord]:
    out: list[SourceRecord] = []
    for subject, counts in totals.items():
        if not counts["assigned"] > 0:
            continue
        remaining = int(counts["remaining"])
        out.append(SourceRecord(
            source="ixl", kid=kid, class_name=None, title=f"IXL {subject}", due_date=None,
            portal_status="open" if remaining > 0 else "submitted",
            raw_status=f"{remaining} remaining", score=None, link=None,
            external_key=f"ixl:{subject.casefold()}", raw=dict(counts)))
    return out


class IxlAdapter:
    source = "ixl"

    def parse(self, payload: Any, *, resolve_kid: Callable[[str], str]) -> AdapterResult:
        """`payload` is `{kid name: ixl payload}`."""
        if not isinstance(payload, dict):
            return empty_result(self.source, "payload is not a per-child mapping")
        records: list[SourceRecord] = []
        seen: set[str] = set()
        untrusted: set[str] = set()
        counts = {"children without a usable name": 0, "child payloads malformed": 0}
        for name, data in payload.items():
            kid = resolve_name(resolve_kid, text(name))
            if not kid:
                counts["children without a usable name"] += 1
                continue
            seen.add(kid)
            if payload_problem(data):
                counts["child payloads malformed"] += 1
                untrusted.add(kid)
                continue
            records.extend(_records(kid, data["totals"]))
        return AdapterResult(self.source, tuple(records), frozenset(seen), frozenset(seen - untrusted),
                             counted(self.source, counts))
