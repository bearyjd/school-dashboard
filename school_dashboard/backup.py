"""Verified backup/restore of the state dir.

Only an explicit allowlist of data files is ever copied; credentials, env
files and the backups directory itself are never touched. Manifests are
UNAUTHENTICATED integrity records (sha256), not signatures: keep backups
owner-only (0700) and treat them as trusted storage.
"""
import errno
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from school_dashboard import maintenance, paths
from school_dashboard import state as _state
from school_dashboard.atomic import mkdir_private, write_atomic

MANIFEST = "manifest.json"
DB_NAME = "school.db"
STATE_NAME = "school-state.json"
CONFIG_NAME = "config.json"
MANIFEST_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

# allowlisted backup filename -> resolver for its live location
_SOURCES: dict[str, Callable[[], Path]] = {
    DB_NAME: paths.db_path,
    STATE_NAME: paths.state_path,
    "facts.json": paths.facts_path,
    "email-digest.json": paths.email_digest_path,
    "gc-schedule.json": paths.gc_path,
    "sync_meta.json": paths.sync_meta_path,
    CONFIG_NAME: _state._config_path,
}
ALLOWED_NAMES = frozenset(_SOURCES)
_TABLES = ("items", "digests", "events")


class BackupError(Exception):
    """A refused or failed operation; `code` is the CLI exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


# ── low-level helpers ────────────────────────────────────────────────────────

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tool_version() -> str:
    try:
        from importlib.metadata import version
        return version("school-dashboard")
    except Exception:  # metadata missing in odd installs; version is informational
        return "unknown"


def _replace_file(src, dst) -> None:
    os.replace(src, dst)


def _copy_nofollow(src: Path, dst: Path) -> None:
    """Stream src -> new 0600 file dst, refusing symlinks and non-regular files."""
    try:
        sfd = os.open(src, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise BackupError(f"{src.name} is a symlink; refusing to follow it") from exc
        raise BackupError(f"cannot open {src.name}: {exc.strerror}") from exc
    with os.fdopen(sfd, "rb") as s:
        if not os.path.stat.S_ISREG(os.fstat(s.fileno()).st_mode):
            raise BackupError(f"{src.name} is not a regular file")
        dfd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
        with os.fdopen(dfd, "wb") as d:
            shutil.copyfileobj(s, d, 1 << 20)
            d.flush()
            os.fsync(d.fileno())


def _copy_db(src: Path, dst: Path) -> None:
    """Consistent SQLite snapshot via the backup API into a new 0600 file."""
    uri = Path(src).resolve().as_uri() + "?mode=ro"
    os.close(os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600))
    with closing(sqlite3.connect(uri, uri=True)) as s, closing(sqlite3.connect(dst)) as d:
        s.backup(d)
        d.execute("PRAGMA journal_mode=DELETE")


def _integrity_result(db: Path) -> str:
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]


def _check_integrity(db: Path) -> None:
    result = _integrity_result(db)
    if result != "ok":
        raise BackupError(f"sqlite integrity_check failed: {result}")


def _table_counts(db: Path) -> dict:
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] if t in existing else 0
                for t in _TABLES}


def _state_counts(p: Path) -> dict:
    data = json.loads(p.read_text())
    if not isinstance(data, dict):
        raise BackupError(f"{p.name} must contain a JSON object")
    items, children = data.get("action_items", []), data.get("children", {})
    return {"action_items": len(items) if isinstance(items, list) else 0,
            "children": len(children) if isinstance(children, (list, dict)) else 0}


def _counts_for(files_dir: Path, names: set) -> dict:
    """Counts read from the copies in files_dir (the snapshot, never the live source)."""
    counts: dict = {}
    try:
        if DB_NAME in names:
            c = _table_counts(files_dir / DB_NAME)
            counts.update({"items": c["items"], "digests": c["digests"], "events": c["events"]})
        if STATE_NAME in names:
            counts.update(_state_counts(files_dir / STATE_NAME))
    except BackupError:
        raise
    except (ValueError, RecursionError, AttributeError, TypeError, OSError, sqlite3.Error) as exc:
        raise BackupError(f"cannot read counts from data files: {type(exc).__name__}: {exc}") from exc
    return counts


# ── backup ───────────────────────────────────────────────────────────────────

def _wait_idle(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while maintenance.sync_in_flight():
        if time.monotonic() >= deadline:
            raise BackupError(
                "a sync appears to be running (sync.running marker); wait for it or use --wait-idle", 2)
        time.sleep(0.2)


def _stage_file(name: str, src: Path, staging: Path) -> None:
    if src.is_symlink():
        raise BackupError(f"{name}: source {src} is a symlink; refusing to back it up")
    dst = staging / name
    try:
        if name == DB_NAME:
            _copy_db(src, dst)
            _check_integrity(dst)
        else:
            _copy_nofollow(src, dst)
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"could not back up {name}: {exc}") from exc


def create_backup(dest: Optional[Path] = None, wait_idle: float = 0) -> Path:
    """Back up allowlisted files; requires maintenance mode. Returns the backup dir."""
    info = maintenance.status()
    if info is None:
        raise BackupError("backup requires maintenance mode; run: school-state transition maintenance on", 2)
    _wait_idle(wait_idle)
    stamp = datetime.now(timezone.utc)
    dest = Path(dest) if dest else paths.state_dir() / "backups" / stamp.strftime("%Y%m%dT%H%M%S%fZ")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise BackupError(f"backup destination {dest} exists and is not an empty directory")
    staging = dest.parent / f".{dest.name}.partial-{uuid.uuid4().hex[:8]}"
    try:
        mkdir_private(dest.parent)
        staging.mkdir(mode=0o700)
    except OSError as exc:
        raise BackupError(f"cannot create backup destination {dest}: {exc.strerror}") from exc
    try:
        manifest = _build(staging, info, stamp)
        write_atomic(staging / MANIFEST, json.dumps(manifest, indent=2) + "\n")
        if dest.exists():
            dest.rmdir()
        os.replace(staging, dest)
        os.chmod(dest, 0o700)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise BackupError(f"cannot write backup to {dest}: {exc}") from exc
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return dest


def _build(staging: Path, info: dict, stamp: datetime) -> dict:
    files, absent = [], []
    for name, resolve in _SOURCES.items():
        src = resolve()
        if not src.is_file():
            absent.append(name)
            continue
        _stage_file(name, src, staging)
        out = staging / name
        # hash the staged copy: that is exactly what a restore will verify
        files.append({"name": name, "sha256": _sha256(out), "bytes": out.stat().st_size})
    return {
        "version": MANIFEST_VERSION,
        "operation_id": info.get("operation_id"),
        "created_at": stamp.isoformat(),
        "source_state_dir": str(paths.state_dir()),
        "tool_version": _tool_version(),
        "files": files,
        "absent": absent,
        "counts": _counts_for(staging, {f["name"] for f in files}),
    }


# ── restore ──────────────────────────────────────────────────────────────────

def _load_manifest(src: Path) -> dict:
    mp = src / MANIFEST
    try:
        with open(mp, "rb") as f:
            raw = f.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise BackupError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
        m = json.loads(raw)
    except OSError as exc:
        raise BackupError(f"cannot read {mp}: {exc.strerror}") from exc
    except (ValueError, RecursionError) as exc:
        raise BackupError(f"manifest is not valid JSON: {type(exc).__name__}") from exc
    if not isinstance(m, dict) or not isinstance(m.get("files"), list) or not isinstance(m.get("counts"), dict):
        raise BackupError("manifest is malformed")
    return m


def _validate_entries(m: dict) -> dict:
    """Return {name: entry}; reject non-allowlisted, path-like or duplicate names."""
    entries: dict = {}
    for e in m["files"]:
        name = e.get("name") if isinstance(e, dict) else None
        if (not isinstance(name, str) or name != os.path.basename(name)
                or name in ("", ".", "..") or "\\" in name or name not in ALLOWED_NAMES):
            raise BackupError(f"manifest contains disallowed file name: {name!r}")
        if name in entries:
            raise BackupError(f"manifest lists {name} twice")
        if not isinstance(e.get("sha256"), str) or not isinstance(e.get("bytes"), int):
            raise BackupError(f"manifest entry for {name} is malformed")
        entries[name] = e
    return entries


def _stage_verified(name: str, src: Path, staging: Path, entry: dict) -> None:
    """Copy first, then hash the COPY, so what is verified is what gets installed."""
    dst = staging / name
    _copy_nofollow(src, dst)
    if dst.stat().st_size != entry["bytes"] or _sha256(dst) != entry["sha256"]:
        raise BackupError(f"sha256/size mismatch for {name}; backup is corrupt or tampered")


def _check_target(target: Path, confirm_live: bool) -> bool:
    """Validate the target; return True when it is the live state dir."""
    live = target.resolve() == paths.state_dir().resolve()
    if live:
        if not maintenance.is_active():
            raise BackupError("restoring to the live state dir requires maintenance mode", 2)
        if not confirm_live:
            raise BackupError("restoring to the live state dir requires --confirm-live", 2)
    elif target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupError(f"restore target {target} must be empty or not exist", 2)
    return live


def _destination(name: str, target: Path, live: bool) -> Optional[Path]:
    if not live:
        return target / name
    if name == CONFIG_NAME:
        return None  # roster config lives outside the state dir; never overwritten
    return _SOURCES[name]()


def _verify_staged(staging: Path, entries: dict, counts: dict) -> dict:
    """Integrity-check the staged DB and compare counts to the manifest."""
    if DB_NAME in entries:
        _check_integrity(staging / DB_NAME)
    actual = _counts_for(staging, set(entries))
    for key, value in actual.items():
        if counts.get(key) != value:
            raise BackupError(f"count mismatch for {key}: manifest={counts.get(key)} restored={value}")
    return actual


def _install(src: Path, dst: Path) -> None:
    """Copy src to a private temp file beside dst, then atomically replace dst."""
    mkdir_private(dst.parent)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.", suffix=".restore-tmp")
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out, 1 << 20)
            out.flush()
            os.fsync(out.fileno())
        _replace_file(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _drop_wal(db: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)


def _snapshot_live(plan: list, snapdir: Path) -> None:
    mkdir_private(snapdir)
    for name, dst in plan:
        if not dst.is_file():
            continue
        if name == DB_NAME:
            _copy_db(dst, snapdir / name)
        else:
            shutil.copyfile(dst, snapdir / name)
            os.chmod(snapdir / name, 0o600)


def _rollback(attempted: list, snapdir: Optional[Path]) -> list:
    """Undo every attempted install, continuing past failures; returns the errors."""
    errors = []
    for name, dst in reversed(attempted):
        try:
            if name == DB_NAME:
                _drop_wal(dst)
            snap = snapdir / name if snapdir else None
            if snap is not None and snap.is_file():
                _install(snap, dst)
            else:
                dst.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{name}: {exc}")
    return errors


def _publish(staging: Path, names: list, target: Path, live: bool) -> Optional[Path]:
    """Install staged files (DB last). On failure, restore the previous state."""
    order = sorted(n for n in names if n != DB_NAME) + ([DB_NAME] if DB_NAME in names else [])
    plan = [(n, d) for n in order if (d := _destination(n, target, live)) is not None]
    snapdir = None
    if live:
        snapdir = target / f".pre-restore-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
        _snapshot_live(plan, snapdir)
    attempted: list = []
    try:
        for name, dst in plan:
            attempted.append((name, dst))
            if name == DB_NAME:
                _drop_wal(dst)
            _install(staging / name, dst)
    except BaseException as exc:
        rb_errors = _rollback(attempted, snapdir)
        if rb_errors:
            raise BackupError(
                f"restore failed ({exc}) and rollback failed for: {'; '.join(rb_errors)}"
                + (f"; previous files are in {snapdir}" if snapdir else "")) from exc
        raise
    return snapdir


def restore_backup(src: Path, target: Path, confirm_live: bool = False) -> dict:
    """Verify then restore. On any failure the target is rolled back to its prior state."""
    src, target = Path(src), Path(target)
    m = _load_manifest(src)
    entries = _validate_entries(m)
    live = _check_target(target, confirm_live)
    created_target = not target.exists()
    staging = Path(tempfile.mkdtemp(dir=target if target.exists() else None, prefix=".restore-staging-"))
    try:
        for name, entry in entries.items():
            _stage_verified(name, src / name, staging, entry)
        actual = _verify_staged(staging, entries, m["counts"])
        mkdir_private(target)
        snapdir = _publish(staging, list(entries), target, live)
    except (OSError, sqlite3.Error) as exc:
        if created_target and target.is_dir() and not any(target.iterdir()):
            target.rmdir()
        raise BackupError(f"restore failed: {exc}") from exc
    except BackupError:
        if created_target and target.is_dir() and not any(target.iterdir()):
            target.rmdir()
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"files": sorted(entries), "counts": actual, "live": live,
            "snapshot": str(snapdir) if snapdir else None}
