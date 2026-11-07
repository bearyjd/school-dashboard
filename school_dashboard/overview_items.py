"""Item normalisation and status rules for /api/overview (pure functions)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, tzinfo
from typing import Optional

from school_dashboard.powerschool import _clean_text
from school_dashboard.state import canonical_name

ROLLOVER_HOUR = 4
MAX_PER_STATUS = 500
_DONE_STATUSES = frozenset({"submitted", "graded", "complete", "completed", "turned in"})
_FREEFORM = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})")


def local_today(now: datetime, tz: tzinfo, rollover_hour: int = ROLLOVER_HOUR) -> date:
    """Local date, where the day rolls over at `rollover_hour` rather than midnight."""
    return (now.astimezone(tz) - timedelta(hours=rollover_hour)).date()


def next_school_day(today: date) -> date:
    """Next weekday after `today`; Fri/Sat/Sun all give Monday. Holidays ignored."""
    nxt = today + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def parse_date(value: object) -> Optional[date]:
    """ISO date (or datetime, by its date part) or 'Month D, YYYY'; anything else is None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    match = _FREEFORM.search(text)
    if match:
        try:
            return datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
        except ValueError:
            return None
    return None


def classify(due: Optional[str], today: date, nxt: date, *, flag: Optional[str] = None,
             done: bool = False) -> Optional[str]:
    """overdue | missing | late | tonight | upcoming, or None for undated/done rows."""
    if done:
        return None
    if flag in ("missing", "late"):
        return flag
    due_date = parse_date(due)
    if due_date is None:
        return None
    if due_date < today:
        return "overdue"
    if due_date <= nxt:
        return "tonight"
    return "upcoming"


def _kid(name: object, kids: list[str]) -> str:
    canon = canonical_name(name) if isinstance(name, str) else ""
    by_lower = {k.lower(): k for k in kids}
    return by_lower.get(canon.lower(), canon)


def _row(*, id: str, kid: str, title: object, course: object, due: object, status: str,
         source: str, category: object = None, score: object = None, url: object = None,
         readonly: bool = False) -> dict:
    due_date = parse_date(due)
    return {
        "id": id,
        "kid": kid,
        "title": _clean_text(title) or "Untitled",
        "course": _clean_text(course) or "",
        "due": due_date.isoformat() if due_date else None,
        "status": status,
        "source": source,
        "category": _clean_text(category),
        "score": _clean_text(score),
        "url": _clean_text(url) or "",
        "readonly": readonly,
    }


def _dict_items(section: object) -> list[tuple[str, dict]]:
    if not isinstance(section, dict):
        return []
    return [(k, v) for k, v in section.items() if isinstance(v, dict)]


def _dict_list(value: object) -> list[dict]:
    return [a for a in value if isinstance(a, dict)] if isinstance(value, list) else []


def _schoology_rows(state: dict, today: date, nxt: date, kids: list[str]) -> list[dict]:
    rows = []
    for child, data in _dict_items(state.get("schoology")):
        kid = _kid(child, kids)
        for i, a in enumerate(_dict_list(data.get("assignments"))):
            done = str(a.get("status") or "").lower() in _DONE_STATUSES
            status = classify(a.get("due_date"), today, nxt, done=done)
            if status is None:
                continue
            rows.append(_row(id=f"sgy:{kid}:{i}", kid=kid, title=a.get("title"),
                             course=a.get("course"), due=a.get("due_date"), status=status,
                             source="schoology", url=a.get("link")))
    return rows


def _powerschool_rows(state: dict, today: date, nxt: date, kids: list[str]) -> list[dict]:
    rows = []
    for child, data in _dict_items(state.get("powerschool")):
        kid = _kid(child, kids)
        for key, flag in (("missing", "missing"), ("late", "late"), ("upcoming", None)):
            for i, a in enumerate(_dict_list(data.get(key))):
                status = classify(a.get("due"), today, nxt, flag=flag)
                if status is None:
                    continue
                rows.append(_row(id=f"ps:{kid}:{key}:{i}", kid=kid, title=a.get("title"),
                                 course=a.get("course"), due=a.get("due"), status=status,
                                 source="powerschool", category=a.get("category"),
                                 score=a.get("score")))
    return rows


def _remaining(data: dict) -> int:
    total = 0
    for _, vals in _dict_items(data.get("totals")):
        remaining = vals.get("remaining")
        if isinstance(remaining, (int, float)) and remaining > 0:
            total += int(remaining)
    return total


def _ixl_rows(state: dict, kids: list[str]) -> list[dict]:
    rows = []
    for child, data in _dict_items(state.get("ixl")):
        remaining = _remaining(data)
        if remaining <= 0:
            continue
        kid = _kid(child, kids)
        rows.append(_row(id=f"ixl:{kid}", kid=kid, title=f"IXL: {remaining} remaining",
                         course="IXL", due=None, status="tonight", source="ixl",
                         readonly=True))
    return rows


def _manual_rows(manual_items: list[dict], today: date, nxt: date, kids: list[str]) -> list[dict]:
    rows = []
    for item in _dict_list(manual_items):
        status = classify(item.get("due_date"), today, nxt, done=bool(item.get("completed")))
        if status is None:
            continue
        rows.append(_row(id=f"manual:{item.get('id')}", kid=_kid(item.get("child"), kids),
                         title=item.get("title"), course="", due=item.get("due_date"),
                         status=status, source="manual", category=item.get("type")))
    return rows


def _cap(rows: list[dict]) -> list[dict]:
    seen = {"overdue": 0, "missing": 0}
    out = []
    for row in rows:
        status = row["status"]
        if status in seen:
            seen[status] += 1
            if seen[status] > MAX_PER_STATUS:
                continue
        out.append(row)
    return out


def build_items(state: dict, manual_items: list[dict], today: date, kids: list[str]) -> list[dict]:
    """Normalised items from all sources; Overdue/Missing are capped at 500 each."""
    state = state if isinstance(state, dict) else {}
    nxt = next_school_day(today)
    rows = (_schoology_rows(state, today, nxt, kids)
            + _powerschool_rows(state, today, nxt, kids)
            + _ixl_rows(state, kids)
            + _manual_rows(manual_items, today, nxt, kids))
    return _cap(rows)
