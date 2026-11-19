"""I/O shell of the shadow ingest: scrape files in, calendar rows in, bookkeeping out.

The only module of the ingest that touches files; the engine itself receives parsed results.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from school_dashboard import powerschool

from . import repo, schema
from .adapters import ADAPTERS, AdapterResult
from .adapters.powerschool import PowerSchoolAdapter

MAX_SCRAPE_BYTES = 20 * 1024 * 1024

Resolve = Callable[[str], str]
Loaded = tuple[AdapterResult | None, str | None, str | None]  # (result, file hash, warning)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> tuple[bytes | None, Any, str | None]:
    """(bytes, payload, problem)."""
    try:
        with path.open("rb") as fh:
            raw = fh.read(MAX_SCRAPE_BYTES + 1)  # bounded read: never trusts a size seen before reading
    except OSError:
        return None, None, "file missing or unreadable"
    if len(raw) > MAX_SCRAPE_BYTES:
        return None, None, "file too large"
    try:
        return raw, json.loads(raw), None
    except (ValueError, RecursionError):
        return raw, None, "file is not valid JSON"


def _fresh(payload: Any, path: Path, now: datetime) -> bool:
    """PowerSchool age rule shared with v1 (scrape_time, MAX_SCRAPE_AGE_HOURS, MAX_FUTURE_SECONDS)."""
    stamp_raw = payload.get("scraped_at") if isinstance(payload, dict) else None
    try:
        stamp = powerschool.scrape_time(stamp_raw, path.stat().st_mtime)
    except OSError:
        return False
    delta = (now.astimezone().replace(tzinfo=None) - stamp).total_seconds()
    return -powerschool.MAX_FUTURE_SECONDS <= delta <= powerschool.MAX_SCRAPE_AGE_HOURS * 3600


def _load_sgy(path: str, resolve: Resolve, now: datetime) -> Loaded:
    raw, payload, problem = _read_json(Path(path))
    if problem or raw is None:
        return None, None, f"schoology: {problem}"
    return ADAPTERS["schoology"].parse(payload, resolve_kid=resolve), _sha(raw), None


def _load_ps(path: str, resolve: Resolve, now: datetime) -> Loaded:
    p = Path(path)
    raw, payload, problem = _read_json(p)
    if problem or raw is None:
        return None, None, f"powerschool: {problem}"
    clean = not powerschool.scrape_problem(raw.decode("utf-8", "replace")) and _fresh(payload, p, now)
    return PowerSchoolAdapter().parse(payload, resolve_kid=resolve, file_ok=clean), _sha(raw), None


def _load_ixl(path: str, resolve: Resolve, now: datetime) -> Loaded:
    folder = Path(path)
    files = sorted(folder.glob("*-assigned.json")) if folder.is_dir() else []
    payload: dict[str, Any] = {}
    digest = hashlib.sha256()
    for f in files:
        raw, data, problem = _read_json(f)
        if problem or raw is None:
            continue
        payload[f.stem.replace("-assigned", "")] = data
        digest.update(f.name.encode() + raw)
    if not payload:
        return None, None, "ixl: no readable files"
    return ADAPTERS["ixl"].parse(payload, resolve_kid=resolve), digest.hexdigest(), None


def load_results(args: Any, resolve: Resolve, now: datetime) -> tuple[list[AdapterResult], dict[str, str], list[str]]:
    """Parse the files `school-state update` just used. A missing or unreadable file adds a warning only."""
    results: list[AdapterResult] = []
    hashes: dict[str, str] = {}
    warnings: list[str] = []
    for source, loader, path in (("schoology", _load_sgy, args.sgy_file), ("powerschool", _load_ps, args.ps_file),
                                 ("ixl", _load_ixl, args.ixl_dir)):
        result, digest, warning = loader(path, resolve, now)
        if result is not None and digest is not None:
            results.append(result)
            hashes[source] = digest
        if warning:
            warnings.append(warning)
    return results, hashes, warnings


def read_events(db_path: str) -> list[dict[str, Any]]:
    """Calendar rows (date, type, child) for school-day arithmetic; [] when the table is not there."""
    try:
        conn = schema.connect(db_path, readonly=True)
    except sqlite3.Error:
        return []
    try:
        return [dict(r) for r in conn.execute("SELECT date, type, child FROM events")]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def mark_ingest_ok(db_path: str, now: datetime) -> None:
    conn = schema.connect(db_path)
    try:
        with repo.transaction(conn):
            repo.set_meta(conn, "last_ingest_ok", now.isoformat())
    finally:
        conn.close()
