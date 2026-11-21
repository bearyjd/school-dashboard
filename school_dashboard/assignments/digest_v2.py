"""Deterministic morning/night digest text from the read model (PURE: no I/O, no clock, no env, no LLM).

Input is `readmodel.build_summary` output plus the open list items for all kids, so flags are the ones the read
model computed on read with the injected `now`. Items may carry an optional `stage_since` ISO string (added by the
I/O shell) used for the "turned in" / "done" dates. Plain text, no markdown, no emoji.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from itertools import zip_longest
from typing import Any, Mapping, Sequence

from .adapters.base import clean_text
from .dates import parse_date, parse_dt

MAX_BYTES = 3500
FINAL_MAX_BYTES = 3900  # deterministic text + optional AI paragraph + optional Notes line, UTF-8
CAPS = (6, 3, 1)
TITLE_CHARS = 80
CLASS_CHARS = 40
MAX_CARDS = 30
_MODE_WORD = {"night": "Do tonight", "morning": "Due next"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_REMAINING = re.compile(r"^\s*(\d+)")
_IXL_PREFIX = re.compile(r"(?i)^ixl\b\s*[:-]?\s*")
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
    name: str  # raw, matches the items
    label: str  # cleaned, shown
    unverified: bool
    no_records: bool
    do: tuple[_Row, ...]  # assignments only
    wait: tuple[_Row, ...]
    needs: tuple[_Row, ...]
    ixl: tuple[_Row, ...]  # one row per IXL subject with work left (or unknown status)
    error: bool = False  # the kid's section could not be built

    @property
    def clear(self) -> bool:
        return self.error or not (self.do or self.wait or self.needs or self.ixl)

    @property
    def card_rows(self) -> int:
        return len(self.do) + len(self.wait) + len(self.needs) + len(self.ixl)


def _day(value: Any) -> str:
    d = parse_date(value)
    if d is None:
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
    return _Row(_name(item.get("kid")), title, line, f"{label}: {parts}" if parts else label, due)


def _name(value: Any) -> str:
    return clean_text(value, CLASS_CHARS) or "(unnamed)"


def _due(item: Mapping[str, Any]) -> str:
    day = _day(item.get("due"))
    return f"due {day}" if day else ""


def _since(item: Mapping[str, Any], word: str) -> str:
    day = _day(item.get("stage_since"))
    return f"{word} {day}" if day else ""


def _remaining(item: Mapping[str, Any]) -> int | None:
    """IXL questions left, or None when no link carries a leading number."""
    for link in item.get("links") or ():
        match = _REMAINING.match(str(link.get("raw_status") or ""))
        if match:
            return int(match.group(1))
    return None


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


def _ixl_rows(practice: Sequence[Mapping[str, Any]], label: str) -> tuple[_Row, ...]:
    rows = []
    for item in practice:
        title = clean_text(item.get("title"), TITLE_CHARS) or "IXL"
        subject = _IXL_PREFIX.sub("", title) or title
        left = _remaining(item)
        if left == 0:
            continue
        status = "status unknown" if left is None else f"{left} remaining"
        rows.append(_Row(_name(item.get("kid")), title, f"{subject} {status}", f"{label}: {status}", ""))
    return tuple(sorted(rows, key=lambda r: r.line.casefold()))


def _kid(name: str, items: Sequence[Mapping[str, Any]], word: str, bad: bool, no_records: bool) -> _Kid:
    mine = sorted((i for i in items if i.get("kid") == name), key=_order)
    plain = [i for i in mine if i.get("kind") != "practice"]
    practice = [i for i in mine if i.get("kind") == "practice" and i.get("bucket") == "do_tonight"]
    do = tuple(_row(i, word, when=_due(i)) for i in plain if i.get("flag") == "do_tonight")
    wait = tuple(_row(i, "Waiting on teacher",
                      when=_since(i, "turned in" if i.get("stage") == 3 else "done"))
                 for i in plain if i.get("flag") == "waiting_on_teacher")
    needs_items = sorted((i for i in plain if i.get("flag") in _NEEDS_RANK),
                         key=lambda i: (_NEEDS_RANK[str(i["flag"])], *_order(i)))
    return _Kid(name, _name(name), bad or any(i.get("unverified") for i in mine), no_records, do, wait,
                tuple(_needs_row(i) for i in needs_items), _ixl_rows(practice, word))


def _bucket(heading: str, rows: Sequence[_Row], cap: int) -> list[str]:
    if not rows:
        return []
    out = [f"{heading} ({len(rows)})", *(r.line for r in rows[:cap])]
    if len(rows) > cap:
        out.append(f"- +{len(rows) - cap} more")
    return out


def _clear_line(k: _Kid) -> str:
    if k.error:
        return f"{k.label}: could not be loaded"
    if k.no_records:
        return f"{k.label}: nothing on record"
    return f"{k.label}: nothing open (not verified)" if k.unverified else f"{k.label}: all clear"


def _render_kid(k: _Kid, cap: int, word: str) -> str:
    if k.clear:
        return _clear_line(k)
    lines = [k.label, *_bucket(word, k.do, cap), *_bucket("Waiting on teacher", k.wait, cap),
             *_bucket("Needs action", k.needs, cap)]
    if k.ixl:
        lines.append("IXL: " + ", ".join(r.line for r in k.ixl))
    return "\n".join(lines)


def _counts_line(k: _Kid, word: str) -> str:
    if k.clear:
        return _clear_line(k)
    parts = [(word, len(k.do)), ("Waiting", len(k.wait)), ("Needs action", len(k.needs)), ("IXL", len(k.ixl))]
    return f"{k.label}: " + ", ".join(f"{label} {n}" for label, n in parts if n)


def _clock(value: Any, now: datetime) -> str:
    at = parse_dt(value, now.tzinfo or timezone.utc)
    if at is None:
        return "unknown"
    at = at.astimezone(now.tzinfo) if now.tzinfo else at
    hour = at.hour % 12 or 12
    return (f"{_DAYS[at.weekday()]} {_MONTHS[at.month - 1]} {at.day}, "
            f"{hour}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}")


def _header(summary: Mapping[str, Any], now: datetime, no_records: bool) -> list[str]:
    as_of = summary.get("data_as_of")
    clock = _clock(as_of, now) if as_of else "unknown"
    lines = [f"Data as of {clock}" if clock != "unknown" else "Data as of: unknown"]
    if no_records:
        lines.append("No assignments on record yet.")
    names = [_SOURCES.get(str(s)) or clean_text(s, CLASS_CHARS) or "unknown source"
             for s in summary.get("unverified_sources") or ()]
    if not summary.get("ingest_ok", True) and "assignment merge" not in names:
        names.insert(0, "assignment merge")
    if names:
        lines += [f"NOT VERIFIED: {', '.join(names)} not up to date.", "Figures below may be out of date."]
    return lines


def _compose(header: Sequence[str], blocks: Sequence[str]) -> str:
    return "\n".join(header) + "\n\n" + "\n\n".join(blocks)


def _fits(text: str, limit: int) -> bool:
    return len(text.encode("utf-8")) <= limit


def _counts_text(header: Sequence[str], kids: Sequence[_Kid], word: str) -> str:
    """Last ladder step: one counts line per kid; if even that is too big, the tail becomes `+N more kids`."""
    lines = [_counts_line(k, word) for k in kids]
    text = _compose(header, lines)
    for keep in range(len(lines) - 1, -1, -1):
        if _fits(text, MAX_BYTES):
            break
        text = _compose(header, [*lines[:keep], f"+{len(lines) - keep} more kids"])
    return text


def _card(row: _Row) -> Mapping[str, Any]:
    return {"source": "recon", "child": row.kid, "title": row.title, "detail": row.detail, "due_date": row.due,
            "url": "", "done": False}


def _cards(kids: Sequence[_Kid], shown: int) -> tuple[Mapping[str, Any], ...]:
    """At most MAX_CARDS, one kid at a time in roster order (round robin); bucket order kept within a kid."""
    if not shown:
        return ()
    per_kid = [[*k.do[:shown], *k.wait[:shown], *k.needs[:shown], *k.ixl] for k in kids]
    rows = [r for turn in zip_longest(*per_kid) for r in turn if r is not None]
    return tuple(_card(r) for r in rows[:MAX_CARDS])


def final_text(text: str, ai: str, notes: str) -> str:
    """The text handed to ntfy: all parts if they fit FINAL_MAX_BYTES (UTF-8), else without the AI paragraph."""
    full = "\n\n".join(p for p in (text, ai, notes) if p)
    if _fits(full, FINAL_MAX_BYTES):
        return full
    return "\n\n".join(p for p in (text, notes) if p)


def _no_records(summary: Mapping[str, Any], names: Sequence[str]) -> bool:
    counts = summary.get("record_counts")
    return bool(names and summary.get("ingest_ok", True) and isinstance(counts, Mapping)
                and all(counts.get(n) == 0 for n in names))


def build(summary: Mapping[str, Any], items: Sequence[Mapping[str, Any]], *, mode: str, now: datetime) -> DigestV2:
    if mode not in _MODE_WORD:
        raise ValueError(f"unknown digest mode: {mode}")
    word = _MODE_WORD[mode]
    bad = not summary.get("ingest_ok", True)
    names = [str(k["name"]) for k in summary.get("kids") or ()]
    empty = _no_records(summary, names)
    broken = {str(k["name"]) for k in summary.get("kids") or () if k.get("error")}
    kids = [replace(_kid(n, items, word, bad, empty), error=n in broken) for n in names]
    header = _header(summary, now, empty)
    shown = 0
    for cap in CAPS:
        text = _compose(header, [_render_kid(k, cap, word) for k in kids])
        if _fits(text, MAX_BYTES):
            shown = cap
            break
    else:
        text = _counts_text(header, kids, word)
    return DigestV2(text, _cards(kids, shown), len(kids), sum(k.card_rows for k in kids))
