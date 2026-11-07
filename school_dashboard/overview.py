"""Assemble the /api/overview payload (pure; `now` and `tz` are injected)."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Optional

from school_dashboard.overview_events import event_sort_key, gc_events
from school_dashboard.overview_items import MAX_PER_STATUS, build_items_report, local_today
from school_dashboard.sync_meta import last_ok_time
from school_dashboard.watchdog import SOURCES, check_source, hours, parse_ts


def _freshness_entry(source: str, entry: Optional[dict], now: datetime, tz: tzinfo, *,
                     paused: bool = False, first_seen: Optional[datetime] = None) -> dict:
    entry = entry if isinstance(entry, dict) else {}
    last_ok = last_ok_time(entry)
    problem = None if paused else check_source(source, entry, now, tz, first_seen=first_seen)
    parsed = parse_ts(last_ok)
    return {
        "last_ok": last_ok,
        "last_run": entry.get("last_run"),
        "result": entry.get("last_result"),
        "issue": problem.issue if problem else None,
        "stale": bool(problem and problem.issue == "stale"),
        "failing": bool(problem and problem.issue == "failing"),
        "age_hours": hours(now - parsed) if parsed else None,
        "paused": paused,
    }


def _truncation_warnings(dropped: dict) -> list[str]:
    return [f"{status} list truncated to {MAX_PER_STATUS} items ({count} not shown)"
            for status, count in dropped.items() if count > 0]


def build_overview(state: dict, manual_items: list[dict], gc_data: dict, school_events: list[dict],
                   meta: dict, roster: list[dict], now: datetime, tz: tzinfo, *,
                   days: int = 14, include_past_seasons: bool = False,
                   active: Optional[list[str]] = None,
                   first_seen: Optional[dict[str, datetime]] = None) -> dict:
    """Items, events and per-source freshness in one payload.

    `active` is the watchdog's monitored-source list (None = all); other sources are
    reported `paused` and never stale/failing. `first_seen` lets a never-succeeded
    source report `never_ok`, as the watchdog does.
    """
    names = [r["name"] for r in roster]
    today = local_today(now, tz)
    items, dropped = build_items_report(state, manual_items, today, names)
    events = sorted(
        gc_events(gc_data, names, today, days, include_past_seasons=include_past_seasons)
        + list(school_events),
        key=event_sort_key,
    )
    meta = meta if isinstance(meta, dict) else {}
    first_seen = first_seen or {}
    freshness = {s: _freshness_entry(s, meta.get(s), now, tz,
                                     paused=active is not None and s not in active,
                                     first_seen=first_seen.get(s)) for s in SOURCES}
    return {
        "generated_at": now.isoformat(),
        "kids": [{"name": n, "colorIndex": i} for i, n in enumerate(names)],
        "items": items,
        "events": events,
        "freshness": freshness,
        "warnings": _truncation_warnings(dropped),
    }
