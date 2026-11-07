"""Assemble the /api/overview payload (pure; `now` and `tz` are injected)."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Optional

from school_dashboard.overview_events import gc_events
from school_dashboard.overview_items import MAX_PER_STATUS, build_items, local_today
from school_dashboard.sync_meta import last_ok_time
from school_dashboard.watchdog import SOURCES, _hours, _parse, check_source


def _freshness_entry(source: str, entry: Optional[dict], now: datetime, tz: tzinfo) -> dict:
    entry = entry if isinstance(entry, dict) else {}
    last_ok = last_ok_time(entry)
    problem = check_source(source, entry, now, tz)
    parsed = _parse(last_ok)
    return {
        "last_ok": last_ok,
        "last_run": entry.get("last_run"),
        "result": entry.get("last_result"),
        "stale": bool(problem and problem.issue == "stale"),
        "failing": bool(problem and problem.issue == "failing"),
        "age_hours": _hours(now - parsed) if parsed else None,
    }


def _truncation_warnings(items: list[dict]) -> list[str]:
    warnings = []
    for status in ("overdue", "missing"):
        if sum(1 for i in items if i["status"] == status) >= MAX_PER_STATUS:
            warnings.append(f"{status} list truncated to {MAX_PER_STATUS} items")
    return warnings


def build_overview(state: dict, manual_items: list[dict], gc_data: dict, school_events: list[dict],
                   meta: dict, roster: list[dict], now: datetime, tz: tzinfo, *,
                   days: int = 14, include_past_seasons: bool = False) -> dict:
    """Items, events and per-source freshness in one payload."""
    names = [r["name"] for r in roster]
    today = local_today(now, tz)
    items = build_items(state, manual_items, today, names)
    events = sorted(
        gc_events(gc_data, names, today, days, include_past_seasons=include_past_seasons)
        + list(school_events),
        key=lambda e: (e["date"], e["time"]),
    )
    meta = meta if isinstance(meta, dict) else {}
    freshness = {s: _freshness_entry(s, meta.get(s), now, tz) for s in SOURCES}
    return {
        "generated_at": now.isoformat(),
        "kids": [{"name": n, "colorIndex": i} for i, n in enumerate(names)],
        "items": items,
        "events": events,
        "freshness": freshness,
        "warnings": _truncation_warnings(items),
    }
