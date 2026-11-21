"""School-day arithmetic: 04:00 rollover, NO_SCHOOL calendar rows, weekends."""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from school_dashboard.assignments.dates import parse_date

ROLLOVER_HOUR = 4
logger = logging.getLogger(__name__)
_LOGGED = threading.Event()


def school_today(now: datetime) -> date:
    """Wall-clock date in now's zone, rolling over at 04:00 (needs a tz-aware now)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("school_today requires a timezone-aware datetime")
    wall = now.replace(tzinfo=None)
    return (wall - timedelta(hours=ROLLOVER_HOUR)).date()


def _applies_to(child: Any, kid: str) -> bool:
    if child is None or not str(child).strip():
        return True
    names = {part.strip().casefold() for part in str(child).split(",")}
    return kid.strip().casefold() in names


def _row_dates(event_rows: Iterable[Mapping[str, Any]], kind: str | None = None, kid: str | None = None) -> Iterable[date]:
    """Readable dates of the rows (of one type and applying to one kid when given); unreadable rows are skipped."""
    for row in event_rows:
        if kind is not None and row.get("type") != kind:
            continue
        if kid is not None and not _applies_to(row.get("child"), kid):
            continue
        day = parse_date(row.get("date"))
        if day is not None:
            yield day


def no_school_dates(event_rows: Iterable[Mapping[str, Any]], kid: str) -> frozenset[date]:
    return frozenset(_row_dates(event_rows, "NO_SCHOOL", kid))


def calendar_covers(event_rows: Iterable[Mapping[str, Any]], today: date) -> bool:
    return any(day >= today for day in _row_dates(event_rows))


def bad_calendar_rows(event_rows: Iterable[Mapping[str, Any]]) -> int:
    """How many rows have no readable date (they are ignored by the functions above)."""
    return sum(parse_date(row.get("date")) is None for row in event_rows)


def calendar_warning(count: int) -> str | None:
    return f"{count} calendar rows had an unreadable date; ignored" if count else None


def log_bad_calendar(count: int) -> None:
    """One WARNING per process (counts only, never row text)."""
    if count and not _LOGGED.is_set():
        _LOGGED.set()
        logger.warning("school calendar: %d row(s) with an unreadable date ignored", count)


def reset_calendar_log() -> None:
    _LOGGED.clear()


def is_school_day(d: date, no_school: frozenset[date]) -> bool:
    return d.weekday() < 5 and d not in no_school


def next_school_day(d: date, no_school: frozenset[date]) -> date:
    """First school day strictly after d."""
    cur = d + timedelta(days=1)
    while not is_school_day(cur, no_school):
        cur += timedelta(days=1)
    return cur


def add_school_days(d: date, n: int, no_school: frozenset[date]) -> date:
    """d moved forward n school days (n >= 0); d itself is returned for n == 0."""
    if n < 0:
        raise ValueError("n must be >= 0")
    cur = d
    for _ in range(n):
        cur = next_school_day(cur, no_school)
    return cur
