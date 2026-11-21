"""Shared adapter types and pure helpers (no I/O, no clock, no env)."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

from ..model import SourceRecord
from ..normalize import class_key, title_key

_FREEFORM = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})")


@dataclass(frozen=True)
class AdapterResult:
    source: str
    records: tuple[SourceRecord, ...]
    seen_kids: frozenset[str]  # kids with an entry in the payload
    trusted_kids: frozenset[str]  # kids whose entry may be used for ABSENCE inference
    warnings: tuple[str, ...]  # counts / reasons only, no titles or names


class Adapter(Protocol):
    source: str

    def parse(self, payload: Any, *, resolve_kid: Callable[[str], str]) -> AdapterResult: ...


def text(value: Any) -> str:
    """Stripped string, "" for anything that is not a string."""
    return value.strip() if isinstance(value, str) else ""


def clean_text(value: Any, cap: int) -> str:
    """Untrusted text made safe to store and print: whitespace collapsed, control and format characters
    (Unicode Cc, Cf) removed, at most `cap` characters; "" for anything that is not a string."""
    if not isinstance(value, str):
        return ""
    kept = "".join(ch for ch in " ".join(value.split()) if unicodedata.category(ch) not in ("Cc", "Cf"))
    return " ".join(kept.split())[:cap].strip()


def resolve_name(resolve_kid: Callable[[str], str], name: str) -> str:
    """Canonical kid, "" when the name is empty or the resolver fails."""
    if not name:
        return ""
    try:
        resolved = resolve_kid(name)
    except Exception:  # a broken alias table must not take the scrape down
        return ""
    return resolved if isinstance(resolved, str) else ""


def parse_due(value: Any) -> tuple[date | None, bool]:
    """(date, unparseable): ISO date/datetime or 'Month D, YYYY'; empty is (None, False)."""
    raw = text(value)
    if not raw:
        return None, False
    try:
        return date.fromisoformat(raw[:10]), False
    except ValueError:
        pass
    match = _FREEFORM.search(raw)
    if match:
        try:
            return datetime.strptime(" ".join(match.groups()), "%B %d %Y").date(), False
        except ValueError:
            pass
    return None, True


def http_link(value: Any) -> str | None:
    """The link only when it is an absolute http(s) URL."""
    raw = text(value)
    parts = urlparse(raw) if raw else None
    return raw if parts and parts.scheme in ("http", "https") and parts.netloc else None


def hash_key(class_name: str | None, title: str, due: date | None) -> str:
    """Stable `h:` key from class, title and due date (survives a row changing lists)."""
    basis = f"{class_key(class_name)}|{title_key(title)}|{due.isoformat() if due else ''}"
    return "h:" + hashlib.sha1(basis.encode()).hexdigest()[:16]


def counted(source: str, counts: Mapping[str, int]) -> tuple[str, ...]:
    """One 'source: N reason' warning per non-zero counter (no titles, no names)."""
    return tuple(f"{source}: {n} {label}" for label, n in counts.items() if n)


def empty_result(source: str, reason: str) -> AdapterResult:
    return AdapterResult(source, (), frozenset(), frozenset(), (f"{source}: {reason}",))
