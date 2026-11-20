"""Deterministic morning/night digest text from the read model (PURE: no I/O, no clock, no env, no LLM).

Input is `readmodel.build_summary` output plus the open list items for all kids, so flags are the ones the read
model computed on read with the injected `now`. Items may carry an optional `stage_since` ISO string (added by the
I/O shell) used for the "turned in" / "done" dates. Plain text, no markdown, no emoji.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from .adapters.base import clean_text

MAX_BYTES = 3500
CAPS = (6, 3, 1)
TITLE_CHARS = 80
CLASS_CHARS = 40
MAX_CARDS = 30
_MODE_WORD = {"night": "Do tonight", "morning": "Due next"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_REMAINING = re.compile(r"^\s*(\d+)")
_SOURCES = {"recon_ingest": "assignment merge", "schoology": "Schoology", "powerschool": "PowerSchool", "ixl": "IXL"}
_INFO = (("late", " [late in portal]"), ("cleared_unconfirmed", " [cleared, unconfirmed]"),
         ("conflict", " [portals disagree]"))
_NEEDS_RANK = {"disputed": 0, "missing": 1, "done_not_received": 2}


@dataclass(frozen=True)
class DigestV2:
    text: str
    cards: tuple[Mapping[str, Any], ...]
    kids: int
    open_items: int


@dataclass(frozen=True)
class _Row:
    kid: str
    title: str
    line: str
    detail: str
    due: str


@dataclass(frozen=True)
class _Kid:
    name: str
    unverified: bool
    do: tuple[_Row, ...]  # assignments first, then IXL remaining lines
    do_count: int  # assignments only
    ixl: int
    wait: tuple[_Row, ...]
    needs: tuple[_Row, ...]

    @property
    def clear(self) -> bool:
        return not (self.do or self.wait or self.needs)


def _day(value: Any) -> str:
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return ""
    return f"{_MONTHS[d.month - 1]} {d.day}"


def _suffix(item: Mapping[str, Any]) -> str:
    info = item.get("info") or ()
    return (" [not verified]" if item.get("unverified") else "") + "".join(s for k, s in _INFO if k in info)


def _row(item: Mapping[str, Any], label: str, *, prefix: str = "", when: str = "", tail: str = "") -> _Row:
    title = clean_text(item.get("title"), TITLE_CHARS) or "(untitled)"
    parts = ", ".join(p for p in (clean_text(item.get("class"), CLASS_CHARS), when) if p)
    line = f"- {prefix}{title}" + (f" ({parts})" if parts else "") + tail + _suffix(item)
    due = str(item.get("due") or "")
    return _Row(str(item.get("kid") or ""), title, line, f"{label}: {parts}" if parts else label, due)


def _due(item: Mapping[str, Any]) -> str:
    day = _day(item.get("due"))
    return f"due {day}" if day else ""


def _since(item: Mapping[str, Any], word: str) -> str:
    day = _day(item.get("stage_since"))
    return f"{word} {day}" if day else ""


def _remaining(item: Mapping[str, Any]) -> int:
    for link in item.get("links") or ():
        match = _REMAINING.match(str(link.get("raw_status") or ""))
        if match:
            return int(match.group(1))
    return 0


def _order(item: Mapping[str, Any]) -> tuple[bool, str, str]:
    due = str(item.get("due") or "")
    return (not due, due, str(item.get("title") or "").casefold())


def _needs_row(item: Mapping[str, Any]) -> _Row:
    flag = item.get("flag")
    if flag == "disputed":
        return _row(item, "Disputed", prefix="Disputed: ", tail=" - marked turned in, portal says missing")
    if flag == "missing":
        return _row(item, "Missing", prefix="Missing: ", when=_due(item))
    return _row(item, "Done, not received", prefix="Done, not received: ", when=_since(item, "done"))


def _practice_row(item: Mapping[str, Any], n: int, label: str) -> _Row:
    title = clean_text(item.get("title"), TITLE_CHARS) or "IXL"
    line = f"- {title}: {n} remaining"
    return _Row(str(item.get("kid") or ""), title, line, f"{label}: {n} remaining", "")


def _kid(name: str, items: Sequence[Mapping[str, Any]], word: str, bad: bool) -> _Kid:
    mine = sorted((i for i in items if i.get("kid") == name), key=_order)
    plain = [i for i in mine if i.get("kind") != "practice"]
    practice = [(i, _remaining(i)) for i in mine if i.get("kind") == "practice" and i.get("bucket") == "do_tonight"]
    do_items = [i for i in plain if i.get("flag") == "do_tonight"]
    do = tuple(_row(i, word, when=_due(i)) for i in do_items)
    ixl = tuple(_practice_row(i, n, word) for i, n in sorted(practice, key=lambda p: str(p[0].get("title"))) if n > 0)
    wait = tuple(_row(i, "Waiting on teacher",
                      when=_since(i, "turned in" if i.get("stage") == 3 else "done"))
                 for i in plain if i.get("flag") == "waiting_on_teacher")
    needs_items = sorted((i for i in plain if i.get("flag") in _NEEDS_RANK),
                         key=lambda i: (_NEEDS_RANK[str(i["flag"])], *_order(i)))
    return _Kid(name, bad or any(i.get("unverified") for i in mine), do + ixl, len(do_items), len(ixl), wait,
                tuple(_needs_row(i) for i in needs_items))


def _bucket(heading: str, count: int, rows: Sequence[_Row], cap: int) -> list[str]:
    if not rows:
        return []
    out = [f"{heading} ({count})", *(r.line for r in rows[:cap])]
    if len(rows) > cap:
        out.append(f"- +{len(rows) - cap} more")
    return out


def _render_kid(k: _Kid, cap: int, word: str) -> str:
    if k.clear:
        return f"{k.name}: nothing open (not verified)" if k.unverified else f"{k.name}: all clear"
    lines = [k.name, *_bucket(word, k.do_count, k.do, cap), *_bucket("Waiting on teacher", len(k.wait), k.wait, cap),
             *_bucket("Needs action", len(k.needs), k.needs, cap)]
    return "\n".join(lines)


def _counts_line(k: _Kid, word: str) -> str:
    if k.clear:
        return _render_kid(k, 1, word)
    parts = [(word, k.do_count), ("Waiting", len(k.wait)), ("Needs action", len(k.needs)), ("IXL", k.ixl)]
    return f"{k.name}: " + ", ".join(f"{label} {n}" for label, n in parts if n)


def _clock(value: Any, now: datetime) -> str:
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError:
        return "unknown"
    at = at.astimezone(now.tzinfo) if at.tzinfo and now.tzinfo else at
    hour = at.hour % 12 or 12
    return f"{_DAYS[at.weekday()]} {hour}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}"


def _header(summary: Mapping[str, Any], now: datetime) -> list[str]:
    as_of = summary.get("data_as_of")
    lines = [f"Data as of {_clock(as_of, now)}" if as_of else "Data as of: unknown"]
    names = [_SOURCES.get(str(s), str(s)) for s in summary.get("unverified_sources") or ()]
    if not summary.get("ingest_ok", True) and "assignment merge" not in names:
        names.insert(0, "assignment merge")
    if names:
        lines += [f"NOT VERIFIED: {', '.join(names)} not up to date.", "Figures below may be out of date."]
    return lines


def _compose(header: Sequence[str], blocks: Sequence[str]) -> str:
    return "\n".join(header) + "\n\n" + "\n\n".join(blocks)


def _card(row: _Row) -> Mapping[str, Any]:
    return {"source": "recon", "child": row.kid, "title": row.title, "detail": row.detail, "due_date": row.due,
            "url": "", "done": False}


def build(summary: Mapping[str, Any], items: Sequence[Mapping[str, Any]], *, mode: str, now: datetime) -> DigestV2:
    if mode not in _MODE_WORD:
        raise ValueError(f"unknown digest mode: {mode}")
    word = _MODE_WORD[mode]
    bad = not summary.get("ingest_ok", True)
    kids = [_kid(str(k["name"]), items, word, bad) for k in summary.get("kids") or ()]
    header = _header(summary, now)
    shown = 0
    text = ""
    for cap in CAPS:
        text = _compose(header, [_render_kid(k, cap, word) for k in kids])
        if len(text.encode("utf-8")) <= MAX_BYTES:
            shown = cap
            break
    else:
        text = _compose(header, [_counts_line(k, word) for k in kids])
    rows = [r for k in kids for r in (*k.do[:shown], *k.wait[:shown], *k.needs[:shown])]
    open_items = sum(len(k.do) + len(k.wait) + len(k.needs) for k in kids)
    return DigestV2(text, tuple(_card(r) for r in rows[:MAX_CARDS]), len(kids), open_items)
