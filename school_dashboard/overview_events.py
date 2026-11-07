"""GameChanger and school-calendar event normalisation for /api/overview."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from school_dashboard.overview_items import parse_date
from school_dashboard.powerschool import _clean_text

_log = logging.getLogger(__name__)


def split_kids(label: str, roster: list[str]) -> list[str]:
    """Roster names named by a team label ('Penn+Jack'); unmatched labels give []."""
    if not isinstance(label, str):
        return []
    by_lower = {name.lower(): name for name in roster}
    found = [by_lower[p.strip().lower()] for p in label.split("+") if p.strip().lower() in by_lower]
    return list(dict.fromkeys(found))


def _gc_event(evt: dict, evt_date: date, kids: list[str], team: dict, source: str) -> dict:
    opponent = _clean_text(evt.get("opponent")) or ""
    kind = _clean_text(evt.get("type")) or ""
    return {
        "kids": kids,
        "title": (f"vs {opponent}" if opponent else kind or "Event")[:160],
        "date": evt_date.isoformat(),
        "time": _clean_text(evt.get("time")) or "",
        "type": kind,
        "location": _clean_text(evt.get("location")) or "",
        "source": "gc",
        "scheduleSource": source,
        "team": _clean_text(team.get("team_name")) or None,
    }


def _team_events(team: dict) -> list[tuple[dict, date]]:
    schedule = team.get("schedule")
    if not isinstance(schedule, list):
        return []
    out = []
    for evt in schedule:
        evt_date = parse_date(evt.get("date")) if isinstance(evt, dict) else None
        if evt_date is not None:
            out.append((evt, evt_date))
    return out


def gc_events(gc_data: dict, roster: list[str], today: date, days: int, *,
              include_past_seasons: bool = False) -> list[dict]:
    """Events in [today, today+days) sorted by date/time; past-season teams hidden by default."""
    teams = gc_data.get("teams") if isinstance(gc_data, dict) else None
    if not isinstance(teams, list):
        return []
    end = today + timedelta(days=days)
    out: list[dict] = []
    for team in teams:
        if not isinstance(team, dict):
            continue
        events = _team_events(team)
        active = any(d >= today for _, d in events)
        if active:
            events = [(e, d) for e, d in events if today <= d < end]
        elif not include_past_seasons:
            continue
        kids = split_kids(team.get("child") or team.get("team_name") or "", roster)
        source = "ics" if team.get("schedule_source") == "ics" else "api"
        out.extend(_gc_event(e, d, kids, team, source) for e, d in events)
    return sorted(out, key=lambda e: (e["date"], e["time"]))


def school_events(db_path: str, today: date, days: int) -> list[dict]:
    """Rows of the `events` table within [today, today+days); [] when the DB/table is absent."""
    if not db_path or not Path(db_path).exists():
        return []
    end = today + timedelta(days=days)
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT date, title, type, child FROM events WHERE date >= ? AND date < ?"
                " ORDER BY date, id",
                (today.isoformat(), end.isoformat()),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        _log.warning("school events unavailable: %s", exc)
        return []
    out = []
    for raw_date, title, kind, child in rows:
        evt_date: Optional[date] = parse_date(raw_date)
        if evt_date is None:
            continue
        out.append({
            "kids": [_clean_text(child)] if _clean_text(child) else [],
            "title": _clean_text(title) or "Event",
            "date": evt_date.isoformat(),
            "time": "",
            "type": _clean_text(kind) or "",
            "location": "",
            "source": "school",
            "scheduleSource": None,
            "team": None,
        })
    return out
