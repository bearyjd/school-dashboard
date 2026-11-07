"""Item normalisation and status rules for /api/overview (pure functions)."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timedelta, tzinfo
from typing import Optional
from urllib.parse import urlparse

from school_dashboard.powerschool import clean_text
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


def _safe_url(url: object) -> str:
    """http(s) URLs only; anything else (javascript:, data:, relative) becomes ''."""
    text = clean_text(url) or ""
    return text if urlparse(text).scheme in ("http", "https") else ""


def _row(*, kid: str, title: object, course: object, due: object, status: str,
         source: str, category: object = None, score: object = None, url: object = None,
         readonly: bool = False, item_id: Optional[int] = None) -> dict:
    due_date = parse_date(due)
    clean_title = clean_text(title) or "Untitled"
    clean_course = clean_text(course) or ""
    due_iso = due_date.isoformat() if due_date else None
    key = "|".join([source, kid, clean_title, clean_course, due_iso or ""])
    row = {
        "id": f"{source}:{hashlib.sha1(key.encode()).hexdigest()[:12]}",
        "kid": kid,
        "title": clean_title,
        "course": clean_course,
        "due": due_iso,
        "status": status,
        "source": source,
        "category": clean_text(category),
        "score": clean_text(score),
        "url": _safe_url(url),
        "readonly": readonly,
    }
    if item_id is not None:
        row["itemId"] = item_id  # items-table id; lets the UI check off via PATCH /api/items/<id>
    return row


def _schoology_url(link: object) -> str:
    """Absolute http(s) links pass through; site-relative ones are joined to SGY_BASE_URL."""
    text = clean_text(link) or ""
    if text.startswith("/") and not text.startswith("//"):
        base = os.environ.get("SGY_BASE_URL", "").rstrip("/")
        return base + text if base else ""
    return text


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
        for a in _dict_list(data.get("assignments")):
            done = str(a.get("status") or "").lower() in _DONE_STATUSES
            status = classify(a.get("due_date"), today, nxt, done=done)
            if status is None:
                continue
            rows.append(_row(kid=kid, title=a.get("title"),
                             course=a.get("course"), due=a.get("due_date"), status=status,
                             source="schoology", url=_schoology_url(a.get("link"))))
    return rows


def _powerschool_rows(state: dict, today: date, nxt: date, kids: list[str]) -> list[dict]:
    rows = []
    for child, data in _dict_items(state.get("powerschool")):
        kid = _kid(child, kids)
        for key, flag in (("missing", "missing"), ("late", "late"), ("upcoming", None)):
            for a in _dict_list(data.get(key)):
                status = classify(a.get("due"), today, nxt, flag=flag)
                # Overdue is Schoology + manual only; a past-due PS "upcoming" row is stale.
                if status is None or status == "overdue":
                    continue
                rows.append(_row(kid=kid, title=a.get("title"),
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
        rows.append(_row(kid=kid, title=f"IXL: {remaining} remaining",
                         course="IXL", due=None, status="tonight", source="ixl",
                         readonly=True))
    return rows


def _manual_source(item: dict) -> str:
    """Items-table rows added by the email import are labelled `email`; everything else is `manual`."""
    return "email" if item.get("source") == "email" else "manual"


def _manual_rows(manual_items: list[dict], today: date, nxt: date, kids: list[str]) -> list[dict]:
    rows = []
    for item in _dict_list(manual_items):
        status = classify(item.get("due_date"), today, nxt, done=bool(item.get("completed")))
        if status is None:
            continue
        rows.append(_row(kid=_kid(item.get("child"), kids),
                         title=item.get("title"), course="", due=item.get("due_date"),
                         status=status, source=_manual_source(item), category=item.get("type"),
                         item_id=item["id"] if type(item.get("id")) is int else None))
    return rows


def _urgency(row: dict) -> tuple:
    """Oldest due first, undated last, then by kid."""
    return (row["due"] is None, row["due"] or "", row["kid"])


def _cap(rows: list[dict]) -> tuple[list[dict], dict]:
    """Sort by urgency, keep MAX_PER_STATUS overdue/missing rows, count what was dropped."""
    seen = {"overdue": 0, "missing": 0}
    dropped = {"overdue": 0, "missing": 0}
    out = []
    for row in sorted(rows, key=_urgency):
        status = row["status"]
        if status in seen:
            seen[status] += 1
            if seen[status] > MAX_PER_STATUS:
                dropped[status] += 1
                continue
        out.append(row)
    return out, dropped


def build_items_report(state: dict, manual_items: list[dict], today: date,
                       kids: list[str]) -> tuple[list[dict], dict]:
    """(items, dropped): dropped counts rows cut by the Overdue/Missing cap."""
    state = state if isinstance(state, dict) else {}
    nxt = next_school_day(today)
    rows = (_schoology_rows(state, today, nxt, kids)
            + _powerschool_rows(state, today, nxt, kids)
            + _ixl_rows(state, kids)
            + _manual_rows(manual_items, today, nxt, kids))
    return _cap(rows)


def build_items(state: dict, manual_items: list[dict], today: date, kids: list[str]) -> list[dict]:
    """Normalised items from all sources; Overdue/Missing are capped at 500 each."""
    return build_items_report(state, manual_items, today, kids)[0]
