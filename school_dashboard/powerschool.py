"""PowerSchool source helpers: scrape cleaning/trust checks and deterministic text blocks.

State shape (written by state.update_from_ps_file):
  state["powerschool"][child] = {"updated", "missing": [A], "late": [A], "upcoming": [A]}
  state["powerschool_meta"]   = {"warnings": [str]}
with A = {"course", "title", "due", "category", "score"}.
"""
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

MAX_SCRAPE_AGE_HOURS = 24
_FIELDS = ("course", "title", "due", "category", "score")
_CATEGORIES = (("missing", "MISSING"), ("late", "LATE"), ("upcoming", "DUE SOON"))


def skip_reason() -> str:
    """Why the scraper cannot run here ("" when it can): not installed or no credentials."""
    if shutil.which("powerschool") is None:
        return "powerschool command not found"
    if not os.environ.get("PS_USERNAME") and not (Path.home() / ".powerschool" / ".env").is_file():
        return "no PS_USERNAME or ~/.powerschool/.env"
    return ""


def clean_assignments(items: object) -> list:
    """New list of normalised assignment dicts; non-dict entries are dropped."""
    if not isinstance(items, list):
        return []
    return [{k: a.get(k) for k in _FIELDS} for a in items if isinstance(a, dict)]


def scrape_age_hours(scraped_at: object, file_mtime: float, now: datetime) -> float:
    """Age of the scrape in hours: from its scraped_at stamp, else the file mtime."""
    stamp: Optional[datetime] = None
    if isinstance(scraped_at, str):
        try:
            stamp = datetime.fromisoformat(scraped_at)
        except ValueError:
            stamp = None
    if stamp is None:
        stamp = datetime.fromtimestamp(file_mtime)
    if stamp.tzinfo is not None and now.tzinfo is None:
        stamp = stamp.astimezone().replace(tzinfo=None)
    return (now - stamp).total_seconds() / 3600


def has_data(state: dict) -> bool:
    section = state.get("powerschool")
    return isinstance(section, dict) and any(isinstance(v, dict) for v in section.values())


def _entries(state: dict) -> list:
    section = state.get("powerschool") or {}
    return sorted((k, v) for k, v in section.items() if isinstance(v, dict))


def _item_text(a: dict) -> str:
    course = a.get("course") or ""
    title = a.get("title") or "Untitled"
    due = f" (due {a['due']})" if a.get("due") else ""
    return f"{course} - {title}{due}" if course else f"{title}{due}"


def format_block(state: dict) -> str:
    """Per-child MISSING / LATE / DUE SOON block; "" when there is no PowerSchool data."""
    lines: list = []
    for child, entry in _entries(state):
        parts = [
            f"  {label} ({len(entry.get(key) or [])}): "
            + "; ".join(_item_text(a) for a in entry[key] if isinstance(a, dict))
            for key, label in _CATEGORIES
            if entry.get(key)
        ]
        if parts:
            lines.append(f"{child}:")
            lines.extend(parts)
        else:
            lines.append(f"{child}: nothing missing, late or due soon")
    return "\n".join(lines)


def format_counts(entry: dict) -> str:
    """One-line 'missing N (titles), late N (titles), due soon N (titles)' for the quick check."""
    def part(key: str, label: str) -> str:
        items = [a for a in (entry.get(key) or []) if isinstance(a, dict)]
        titles = ", ".join(a.get("title") or "Untitled" for a in items)
        return f"{label} {len(items)}" + (f" ({titles})" if titles else "")

    return ", ".join(part(k, lbl.lower()) for k, lbl in _CATEGORIES)
