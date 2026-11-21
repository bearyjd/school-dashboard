"""School-day arithmetic: 04:00 rollover, NO_SCHOOL calendar rows, weekends."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

ROLLOVER_HOUR = 4


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


def _row_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def no_school_dates(event_rows: Iterable[Mapping[str, Any]], kid: str) -> frozenset[date]:
    return frozenset(
        _row_date(row["date"])
        for row in event_rows
        if row.get("type") == "NO_SCHOOL" and _applies_to(row.get("child"), kid)
    )


def calendar_covers(event_rows: Iterable[Mapping[str, Any]], today: date) -> bool:
    return any(_row_date(row["date"]) >= today for row in event_rows)


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
