"""Teacher-note drafts: deterministic plain-text templates and a pure mailto builder (no I/O, no clock, no sending).

Every interpolated value is cleaned (control/format characters removed, NFKC-normalised, length-capped).
Guaranteed absent from the output: any whitespace-delimited token that has a scheme ("x:" followed by text), contains
"http" or "www.", or looks like a host name (a word, a dot, then 2+ letters, e.g. "evil.example/login"); and every
"@", "!" and the word "urgent". Ordinary words, numbers, dates ("Nov 16"), decimals ("3.5") and abbreviations with a
trailing dot ("Ms.", "p. 12") survive; a dotted token such as "Ms.Rivera" or "ch.4a" may be dropped.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Callable
from urllib.parse import quote

from .adapters.base import clean_text
from .service_classes import EMAIL_SHAPE

NOTE_FLAGS = ("disputed", "done_not_received", "missing")
MAX_TITLE = 120
MAX_NAME = 120
MAX_MAILTO = 1800
_LINKISH = re.compile(r"\S*(?:https?|www\.)\S*", re.I)
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:\S", re.I)
_HOSTLIKE = re.compile(r"[\w-]+\.[^\W\d_]{2,}")
_DOTS = str.maketrans({"\u3002": ".", "\uff61": "."})
_BANNED = re.compile(r"[@!]|urgent", re.I)
_ADDRESS_BAD = frozenset("?&,;%#\r\n")


@dataclass(frozen=True)
class NoteItem:
    title: str
    class_name: str | None
    marked_on: date | None  # when the stage was set (turned in / done at home)


@dataclass(frozen=True)
class NoteDraft:
    subject: str
    body: str


def _scrub(value: object, cap: int) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value, cap * 3)).translate(_DOTS)
    text = _LINKISH.sub(" ", text)
    kept = " ".join(t for t in text.split() if not (_SCHEME.search(t) or _HOSTLIKE.search(t)))
    return clean_text(_BANNED.sub("", kept), cap)


def _day(value: date) -> str:
    return f"{value:%b} {value.day}"


def _on(prefix: str, value: date | None) -> str:
    return f"{prefix} {_day(value)}" if value else ""


def _disputed(kid: str, what: str, item: NoteItem, today: date) -> tuple[str, ...]:
    return (f"We marked {kid}'s {what} as turned in{_on(' on', item.marked_on)}, but the portal shows it as missing.",
            "Could you please check whether it arrived?",
            "I am happy to send it again if that helps.")


def _done_not_received(kid: str, what: str, item: NoteItem, today: date) -> tuple[str, ...]:
    return (f"{kid} finished {what} at home{_on(' on', item.marked_on)}, and it is not showing as received yet.",
            "Could you please confirm that you have it?",
            "If not, please tell us how to get it to you.")


def _missing(kid: str, what: str, item: NoteItem, today: date) -> tuple[str, ...]:
    return (f"The portal shows {kid}'s {what} as missing as of {_day(today)}.",
            f"{kid} will get it done, and we are also checking at home.",
            "Is there anything else outstanding that we should know about?")


_PARTS: dict[str, Callable[[str, str, NoteItem, date], tuple[str, ...]]] = {
    "disputed": _disputed, "done_not_received": _done_not_received, "missing": _missing}


def draft(item: NoteItem, *, flag: str, parent_name: str, kid: str, teacher_name: str | None,
          today: date) -> NoteDraft:
    """The subject and body for `flag`; ValueError for a flag that has no template."""
    if flag not in _PARTS:
        raise ValueError("no note template for this flag")
    kid_name, parent = _scrub(kid, MAX_NAME), _scrub(parent_name, MAX_NAME)
    title = _scrub(item.title, MAX_TITLE) or "an assignment"
    cls = _scrub(item.class_name, MAX_NAME)
    teacher = _scrub(teacher_name, MAX_NAME)
    what = f'"{title}"' + (f" for {cls}" if cls else "")
    lines = _PARTS[flag](kid_name, what, item, today)
    greeting = f"Hello {teacher}," if teacher else "Hello,"
    body = "\n".join((greeting, "", *lines, "", "Thank you,", parent))
    return NoteDraft(f"{kid_name} - {title}" + (f" ({cls})" if cls else ""), body)


def valid_address(to: str | None) -> bool:
    """The class-settings shape, plain ASCII, and none of the characters that would end or split the address."""
    return (isinstance(to, str) and to.isascii() and len(to) <= 200 and EMAIL_SHAPE.match(to) is not None
            and not any(ch in _ADDRESS_BAD or ch.isspace() for ch in to))


def mailto(to: str | None, subject: str, body: str) -> str | None:
    """RFC 6068 link, or None when the address is doubtful or the link longer than MAX_MAILTO characters.

    Subject and body are percent-encoded whole, so nothing in them can add a header; line breaks are CRLF.
    """
    if not valid_address(to) or to is None:
        return None
    crlf = "\r\n".join(body.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    url = f"mailto:{quote(to, safe='@')}?subject={quote(subject, safe='')}&body={quote(crlf, safe='')}"
    return url if len(url) <= MAX_MAILTO else None
