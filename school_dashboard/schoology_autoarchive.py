"""Conservative auto-archive of Schoology action items that vanished from Schoology.

`state.add_action_item` only adds, and `state.prune_stale` only handles parseable
past due dates, so undated items that disappear upstream would stay pending
forever. Each pending schoology item carries `last_seen` and `missed`; an item
is archived (moved to an append-only JSONL file) only after it was absent from
MISSES_TO_ARCHIVE *trusted* scrapes spanning at least MIN_DAYS_ABSENT days.
"""
import hashlib
import json
import logging
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from school_dashboard import maintenance, state
from school_dashboard.atomic import mkdir_private

logger = logging.getLogger(__name__)

SOURCE = "schoology"
STATE_KEY = "schoology_autoarchive_scrape"
MISSES_TO_ARCHIVE = 3
MAX_SCRAPE_BYTES = 20 * 1024 * 1024
MIN_DAYS_ABSENT = 3
SUSPICIOUS_EMPTY_MIN_PENDING = 3
MASS_CHANGE_MIN_DUE = 3
MASS_CHANGE_FRACTION = 0.5
MASS_CHANGE_ALL_MIN_PENDING = 2


def _parse(stamp: object) -> Optional[datetime]:
    """Naive local datetime, or None when unparseable (aware stamps are converted)."""
    if not isinstance(stamp, str):
        return None
    try:
        dt = datetime.fromisoformat(stamp)
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, OverflowError, OSError):
        return None


def _missed(item: dict) -> int:
    try:
        return max(0, int(item.get("missed") or 0))
    except (TypeError, ValueError):
        return 0


def _future_due(item: dict, now: datetime) -> bool:
    due = item.get("due")
    if not isinstance(due, str):
        return False
    try:
        return datetime.fromisoformat(due[:10]).date() > now.date()
    except ValueError:
        return False


def _is_pending_sgy(item: dict) -> bool:
    return item.get("source") == SOURCE and item.get("status") == "pending"


def _valid_entry(entry: object) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("child"), dict):
        return False
    name = entry["child"].get("name")
    if not isinstance(name, str) or not name.split():
        return False
    assignments = entry.get("assignments", [])
    return isinstance(assignments, list) and all(
        isinstance(a, dict) and isinstance(a.get("title", "Unknown"), str) for a in assignments
    )


def scrape_view(sgy_file: str) -> tuple:
    """(seen: child -> set of item ids, trusted: child -> bool, sha256 of the file bytes).

    Children whose entry is missing or malformed are absent from both maps.
    An unreadable file yields ({}, {}, None).
    """
    try:
        with open(sgy_file, "rb") as f:
            raw = f.read(MAX_SCRAPE_BYTES + 1)
        if len(raw) > MAX_SCRAPE_BYTES:
            logger.warning("schoology auto-archive: scrape file exceeds %d bytes; ignoring", MAX_SCRAPE_BYTES)
            return {}, {}, None
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}, {}, None
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(data, dict):
        return {}, {}, digest
    warnings = data.get("warnings")
    clean = warnings is None or (isinstance(warnings, list) and not warnings)
    seen: dict = {}
    trusted: dict = {}
    for entry in data.get("per_child", []) if isinstance(data.get("per_child"), list) else []:
        if not _valid_entry(entry):
            continue
        name = state.canonical_name(entry["child"]["name"].split()[0])
        ids = {state._action_id(SOURCE, a.get("title", "Unknown"), name) for a in entry.get("assignments", [])}
        seen[name] = seen.get(name, set()) | ids
        trusted[name] = clean and trusted.get(name, True)
    return seen, trusted, digest


def _touch(item: dict, present: bool, trusted: bool, now: datetime) -> dict:
    """The item with last_seen/missed updated for one scrape."""
    if present:
        return {**item, "last_seen": now.isoformat(), "missed": 0}
    seeded = _parse(item.get("last_seen")) is not None
    last_seen = item["last_seen"] if seeded else now.isoformat()
    counts = trusted and not _future_due(item, now)
    return {**item, "last_seen": last_seen, "missed": _missed(item) + (1 if counts else 0)}


def _due_for_archive(item: dict, now: datetime) -> bool:
    last_seen = _parse(item.get("last_seen")) or now
    return (_missed(item) >= MISSES_TO_ARCHIVE and now - last_seen >= timedelta(days=MIN_DAYS_ABSENT)
            and not _future_due(item, now))


def _mass_change(due: int, pending: int) -> bool:
    big = due >= MASS_CHANGE_MIN_DUE and due > MASS_CHANGE_FRACTION * pending
    return big or (due == pending and pending >= MASS_CHANGE_ALL_MIN_PENDING)


def _plan_child(items: list, ids: set, trusted: bool, now: datetime) -> tuple:
    """(updated items keyed by id, archive ids, warnings) for one child's pending items."""
    warnings: list = []
    if trusted and not ids and len(items) >= SUSPICIOUS_EMPTY_MIN_PENDING:
        warnings.append(f"empty scrape for a child with {len(items)} pending schoology items; "
                        "not counting misses")
        trusted = False
    updated = {i["id"]: _touch(i, i["id"] in ids, trusted, now) for i in items}
    due = [k for k, v in updated.items() if _due_for_archive(v, now)]
    if _mass_change(len(due), len(items)):
        warnings.append(f"mass change suspected ({len(due)} of {len(items)} pending items); archiving nothing; "
                        "if this is a real cleanup run `school-state schoology reset --dry-run`")
        reverted = {i["id"]: {**updated[i["id"]], "missed": _missed(i)} for i in items if i["id"] not in ids}
        return {**updated, **reverted}, [], warnings
    return updated, due, warnings


def _plan(st: dict, scrape_seen: dict, trusted: dict, now: datetime) -> tuple:
    """(all items with fields updated, ids to archive, warnings). Pure."""
    by_child: dict = {}
    for item in st["action_items"]:
        if _is_pending_sgy(item) and item.get("child") in scrape_seen:
            by_child.setdefault(item["child"], []).append(item)
    updated: dict = {}
    archive_ids: set = set()
    warnings: list = []
    for child, items in by_child.items():
        upd, due, warns = _plan_child(items, scrape_seen[child], bool(trusted.get(child)), now)
        updated.update(upd)
        archive_ids.update(due)
        warnings.extend(warns)
    items = [updated.get(i.get("id"), i) for i in st["action_items"]]
    return items, archive_ids, warnings


def _archived_entry(item: dict, now: datetime) -> dict:
    days = (now - (_parse(item.get("last_seen")) or now)).days
    return {
        "archived_at": now.isoformat(),
        "reason": f"absent from {_missed(item)} scrapes over {days} days",
        "item": item,
    }


def plan_autoarchive(st: dict, scrape_seen: dict, trusted: dict, now: datetime) -> tuple:
    """Pure: (new_state, archived entries, warnings). `st` is never mutated."""
    items, archive_ids, warnings = _plan(st, scrape_seen, trusted, now)
    gone = [i for i in items if i.get("id") in archive_ids and _is_pending_sgy(i)]
    kept = [i for i in items if not (i.get("id") in archive_ids and _is_pending_sgy(i))]
    archived = [_archived_entry(i, now) for i in gone]
    return {**st, "action_items": kept}, archived, warnings


def archive_path(state_file: Path, now: datetime) -> Path:
    return Path(state_file).parent / "archive" / f"schoology-auto-{now:%Y-%m}.jsonl"


def _prepare_dir(d: Path) -> None:
    """The archive dir must be a real directory we own (0700); created if missing."""
    if not os.path.lexists(d):
        mkdir_private(d)
        _fsync_dir(d.parent)
        return
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise OSError(f"archive dir {d} is a symlink, not a directory, or not owned by this user")
    os.chmod(d, 0o700)


def _fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_all(fd: int) -> bytes:
    size = os.fstat(fd).st_size
    chunks, off = [], 0
    while off < size:
        chunk = os.pread(fd, min(1 << 20, size - off), off)
        if not chunk:
            break
        chunks.append(chunk)
        off += len(chunk)
    return b"".join(chunks)


def _archive_key(item: dict) -> tuple:
    """Identity of one archival: the same item archived again later has a newer last_seen."""
    return (item.get("id"), item.get("last_seen"))


def _archived_ids(existing: bytes) -> set:
    ids: set = set()
    for line in existing.splitlines():
        try:
            ids.add(_archive_key(json.loads(line)["item"]))
        except (ValueError, KeyError, TypeError):
            continue
    return ids


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError("short write to archive file")
        view = view[n:]


def append_archive(path: Path, entries: list) -> None:
    """Append JSONL lines (dir 0700, file 0600), skipping ids already archived.

    Writes everything, fsyncs the file and its directory, and raises OSError on any
    failure so the caller keeps the items pending.
    """
    path = Path(path)
    _prepare_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        existing = _read_all(fd)
        done = _archived_ids(existing)
        fresh = [e for e in entries if _archive_key(e["item"]) not in done]
        if fresh:
            lead = b"\n" if existing and not existing.endswith(b"\n") else b""
            _write_all(fd, lead + "".join(json.dumps(e, sort_keys=True) + "\n" for e in fresh).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(path.parent)


def run_autoarchive(st: dict, sgy_file: str, state_file: Path, now: Optional[datetime] = None) -> tuple:
    """Thin I/O wrapper: (new_state, archived count). Archive is written before removal.

    A scrape file whose hash equals the stored one is the same scrape seen again:
    it never counts a miss nor archives. The hash is stored only after success.
    """
    if maintenance.is_active():
        return st, 0
    now = now or datetime.now()
    seen, trusted, digest = scrape_view(sgy_file)
    if digest is None:
        return st, 0
    stamp = {STATE_KEY: {"hash": digest, "processed_at": now.isoformat()}}
    if (st.get(STATE_KEY) or {}).get("hash") == digest:
        items, _ids, _w = _plan(st, seen, {}, now)
        return {**st, "action_items": items}, 0
    new_state, archived, warnings = plan_autoarchive(st, seen, trusted, now)
    for w in warnings:
        logger.warning("schoology auto-archive: %s", w)
    if archived:
        try:
            append_archive(archive_path(state_file, now), archived)
        except OSError as exc:
            logger.error("schoology auto-archive: archive write failed (%s); keeping items pending", exc)
            return st, 0
    return {**new_state, **stamp}, len(archived)
