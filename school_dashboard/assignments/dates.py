"""Total date parsers for STORED and SCRAPED text: unreadable input is None, never an exception, never a guess."""
from __future__ import annotations

import re
from datetime import date, datetime, tzinfo
from typing import Any

_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2}:\d{2}.*)?$")


def parse_date(value: Any) -> date | None:
    """A date from `YYYY-MM-DD` (optionally followed by `T` or a space and a time); None otherwise."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    match = _ISO_DAY.match(value.strip())
    if match is None:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def parse_dt(value: Any, tz: tzinfo) -> datetime | None:
    """A timezone-aware datetime from an ISO string; naive values are read in `tz`; None when unreadable."""
    if isinstance(value, datetime):
        parsed: datetime | None = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else parsed.replace(tzinfo=tz)
