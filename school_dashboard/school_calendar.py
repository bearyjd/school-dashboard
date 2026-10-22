"""Load an approved school-calendar JSON file into the SQLite `events` table.

Replace semantics: all rows whose source is `calendar:<calendar_id>` are deleted
and re-inserted in a single transaction; rows from other sources are never touched.
"""
import argparse
import json
import re
import sqlite3
import stat
import sys
from collections import Counter
from contextlib import closing
from datetime import date
from pathlib import Path

from school_dashboard import db, maintenance, paths

KINDS = frozenset({"no_school", "half_day", "early_release", "mass", "event"})
REQUIRED_KEYS = ("calendar_id", "roster_grades", "events")
_ID_RE = re.compile(r"[a-z0-9-]+")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_FILE_BYTES = 1024 * 1024
MAX_TITLE = 200
MAX_NAME = 50
MAX_REPR = 80
MAX_ERRORS = 20
EXIT_USAGE = 2
EXIT_MAINTENANCE = 3  # same code cli.py uses for blocked write commands


class CalendarError(Exception):
    """The calendar file is unreadable or invalid; nothing was written."""

    code = EXIT_USAGE


class ImportConflictError(CalendarError):
    """The file is valid but clashes with existing rows; nothing was written."""

    code = 1


def _short(value) -> str:
    text = repr(value)
    return text if len(text) <= MAX_REPR else text[:MAX_REPR] + "..."


def _read_text(path) -> str:
    try:
        st = Path(path).stat()
        if not stat.S_ISREG(st.st_mode):
            raise CalendarError(f"{path} is not a regular file")
        if st.st_size > MAX_FILE_BYTES:
            raise CalendarError(f"{path} is too large ({st.st_size} bytes; limit {MAX_FILE_BYTES})")
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CalendarError(f"cannot read {path}: {exc}") from exc


def load_file(path) -> dict:
    text = _read_text(path)
    try:
        data = json.loads(text)
    except RecursionError as exc:
        raise CalendarError(f"{path} is nested too deeply") from exc
    except json.JSONDecodeError as exc:
        raise CalendarError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CalendarError("top level of calendar file must be a JSON object")
    return data


def _valid_date(value) -> bool:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _bad_text(value, limit: int) -> bool:
    return (not isinstance(value, str) or not value.strip()
            or len(value.strip()) > limit or bool(_CTRL_RE.search(value)))


def _event_errors(i: int, ev, roster: set) -> list[str]:
    if not isinstance(ev, dict):
        return [f"events[{i}]: must be an object"]
    errs = []
    if not _valid_date(ev.get("date")):
        errs.append(f"events[{i}]: date must be a valid ISO date (YYYY-MM-DD), got {_short(ev.get('date'))}")
    if _bad_text(ev.get("title"), MAX_TITLE):
        errs.append(f"events[{i}]: title must be a non-empty string of at most {MAX_TITLE} "
                    f"characters without control characters, got {_short(ev.get('title'))}")
    if ev.get("kind") not in KINDS:
        errs.append(f"events[{i}]: kind must be one of {sorted(KINDS)}, got {_short(ev.get('kind'))}")
    rel = ev.get("relevant_to")
    if not isinstance(rel, list) or any(_bad_text(n, MAX_NAME) for n in rel):
        errs.append(f"events[{i}]: relevant_to must be a list of valid names")
    else:
        unknown = [n for n in rel if n not in roster]
        if unknown:
            errs.append(f"events[{i}]: relevant_to names not in roster_grades: {_short(unknown)}")
    return errs


def _duplicates(events: list) -> list[str]:
    """Errors for repeated (date, title): the events table has a UNIQUE index on them."""
    seen: dict[tuple, int] = {}
    errs = []
    for i, ev in enumerate(events):
        if not ev["relevant_to"]:
            continue  # skipped events are never inserted, so they cannot collide
        key = (ev["date"], ev["title"].strip())
        if key in seen:
            errs.append(f"events[{i}]: duplicate date+title of events[{seen[key]}] "
                        f"(date {key[0]}, title {_short(key[1])})")
        else:
            seen[key] = i
    return errs


def validate(data: dict) -> list[str]:
    """Raise CalendarError listing every problem; return warnings (none today) when valid."""
    errs = [f"missing required key: {k}" for k in REQUIRED_KEYS if k not in data]
    if not errs:
        if not (isinstance(data["calendar_id"], str) and _ID_RE.fullmatch(data["calendar_id"])):
            errs.append("calendar_id must match [a-z0-9-]+")
        roster = data["roster_grades"]
        if not isinstance(roster, dict) or not roster:
            errs.append("roster_grades must be a non-empty object")
        bad_names = [n for n in roster if _bad_text(n, MAX_NAME)] if isinstance(roster, dict) else []
        if bad_names:
            errs.append(f"roster_grades has invalid names: {_short(bad_names)}")
        if not isinstance(data["events"], list) or not data["events"]:
            errs.append("events must be a non-empty list")
    if not errs:
        for i, ev in enumerate(data["events"]):
            errs.extend(_event_errors(i, ev, set(data["roster_grades"])))
        if not errs:
            errs.extend(_duplicates(data["events"]))
    if errs:
        shown = errs[:MAX_ERRORS]
        extra = len(errs) - len(shown)
        tail = [f"... and {extra} more"] if extra else []
        raise CalendarError("invalid calendar: " + "; ".join(shown + tail))
    return []


def build_rows(data: dict) -> tuple[list[tuple], int]:
    """Map validated events to one (date, title, type, child, source) row each; also the skipped count.

    child is None for the whole roster, else the names joined with ", " (one row per event
    because the events table has a UNIQUE index on (date, title)).
    """
    source = f"calendar:{data['calendar_id']}"
    roster = set(data["roster_grades"])
    rows: list[tuple] = []
    skipped = 0
    for ev in data["events"]:
        rel = ev["relevant_to"]
        etype = ev["kind"].upper()
        title = ev["title"].strip()
        if not rel:
            skipped += 1
        else:
            child = None if set(rel) == roster else ", ".join(dict.fromkeys(rel))
            rows.append((ev["date"], title, etype, child, source))
    return rows, skipped


def _insert_rows(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO events (date, title, type, child, source) VALUES (?, ?, ?, ?, ?)", rows)


def _preview_existing(db_file: Path, source: str, rows: list[tuple]) -> int:
    """Read-only: rows a replace would delete; raises ImportConflictError on a predicted collision.

    Returns 0 if the DB or table is absent or unreadable.
    """
    if not db_file.exists():
        return 0
    try:
        with closing(sqlite3.connect(f"{db_file.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM events WHERE source = ?", (source,)).fetchone()[0]
            msg = _find_conflict(conn, source, rows)
    except sqlite3.DatabaseError:
        return 0
    if msg:
        raise ImportConflictError(msg)
    return count


def _replace(db_file: Path, source: str, rows: list[tuple]) -> int:
    db.init_events_table(str(db_file))
    with closing(sqlite3.connect(str(db_file))) as conn:
        with conn:  # one transaction: commit on success, rollback on error
            deleted = conn.execute("DELETE FROM events WHERE source = ?", (source,)).rowcount
            try:
                _insert_rows(conn, rows)
            except sqlite3.IntegrityError as exc:
                raise _conflict_error(conn, source, rows, exc) from exc
    return deleted


def _find_conflict(conn: sqlite3.Connection, source: str, rows: list[tuple]) -> str | None:
    """Message for the first row colliding on (date, title) with a row from another source."""
    for d, title, *_ in rows:
        hit = conn.execute("SELECT source FROM events WHERE date = ? AND title = ? AND source != ?",
                           (d, title, source)).fetchone()
        if hit:
            return (f"cannot import: {d} {_short(title)} already exists from source {hit[0]!r}; "
                    "nothing was imported")
    return None


def _conflict_error(conn: sqlite3.Connection, source: str, rows: list[tuple],
                    exc: sqlite3.IntegrityError) -> ImportConflictError:
    msg = _find_conflict(conn, source, rows)
    return ImportConflictError(msg or f"cannot import: constraint failed ({exc}); nothing was imported")


def _report(data: dict, rows: list[tuple], skipped: int, deleted: int,
            warnings: list[str], dry_run: bool) -> dict:
    dates = sorted(r[0] for r in rows)
    return {
        "dry_run": dry_run,
        "calendar_id": data["calendar_id"],
        "source": f"calendar:{data['calendar_id']}",
        "inserted": len(rows),
        "deleted": deleted,
        "skipped": skipped,
        "by_type": dict(Counter(r[2] for r in rows)),
        "date_range": [dates[0], dates[-1]] if dates else None,
        "warnings": warnings,
    }


def import_calendar(path, db_file, dry_run: bool) -> dict:
    """Validate then import (or, with dry_run, only report). Raises CalendarError before any write."""
    data = load_file(path)
    warnings = validate(data)
    rows, skipped = build_rows(data)
    db_file = Path(db_file)
    source = f"calendar:{data['calendar_id']}"
    deleted = _preview_existing(db_file, source, rows) if dry_run else _replace(db_file, source, rows)
    return _report(data, rows, skipped, deleted, warnings, dry_run)


def render_text(report: dict) -> str:
    verb = ("would insert", "would delete") if report["dry_run"] else ("inserted", "deleted")
    rng = " .. ".join(report["date_range"]) if report["date_range"] else "n/a"
    types = ", ".join(f"{k}={v}" for k, v in sorted(report["by_type"].items())) or "none"
    lines = [
        f"{'DRY RUN: ' if report['dry_run'] else ''}calendar {report['calendar_id']} ({report['source']})",
        f"  {verb[0]}: {report['inserted']}",
        f"  {verb[1]}: {report['deleted']}",
        f"  skipped (no relevant child): {report['skipped']}",
        f"  by type: {types}",
        f"  date range: {rng}",
    ]
    lines.extend(f"  warning: {w}" for w in report["warnings"])
    return "\n".join(lines) + "\n"


def add_parser(subs) -> argparse.ArgumentParser:
    p = subs.add_parser("calendar", help="Load approved school-calendar JSON files")
    cs = p.add_subparsers(dest="calendar_command")
    p_i = cs.add_parser("import", help="Replace this calendar's events in the events table")
    p_i.add_argument("file", type=Path, help="Approved calendar JSON (config/calendars/*.json)")
    p_i.add_argument("--dry-run", action="store_true", help="Read-only report; works in maintenance mode")
    p_i.add_argument("--json", action="store_true", help="Machine-readable output")
    p_i.set_defaults(func=cmd_calendar)
    p.set_defaults(func=cmd_calendar)
    return p


def cmd_calendar(args: argparse.Namespace) -> None:
    if getattr(args, "calendar_command", None) != "import":
        print("usage: school-state calendar import <file> [--dry-run] [--json]", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    # Checked inline (not via the global `writes` flag) so --dry-run still works in maintenance mode.
    if not args.dry_run and maintenance.is_active():
        print("Error: maintenance mode is active; write commands are disabled "
              "(school-state transition maintenance off)", file=sys.stderr)
        sys.exit(EXIT_MAINTENANCE)
    try:
        report = import_calendar(args.file, paths.db_path(), args.dry_run)
    except CalendarError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(exc.code)
    except sqlite3.Error as exc:
        print(f"Error: database problem, nothing imported: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(report, indent=2) if args.json else render_text(report), end="\n" if args.json else "")
