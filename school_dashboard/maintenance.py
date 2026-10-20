"""Maintenance-mode flag: a file in the state dir that pauses all writers.

A corrupt or unreadable flag file is treated as ACTIVE (fail closed).
"""
import json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from school_dashboard import paths
from school_dashboard.atomic import write_atomic

logger = logging.getLogger(__name__)

FLAG_NAME = "maintenance.json"


def flag_path():
    return paths.state_dir() / FLAG_NAME


def status() -> Optional[dict]:
    """The flag contents, None if inactive. Corrupt flags yield {"corrupt": True, ...}."""
    p = flag_path()
    try:
        raw = json.loads(p.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("Maintenance flag %s unreadable (%s); treating as ACTIVE", p, exc)
        return {"operation_id": None, "since": None, "reason": "corrupt maintenance flag", "corrupt": True}
    if not isinstance(raw, dict) or not raw.get("operation_id"):
        logger.warning("Maintenance flag %s malformed; treating as ACTIVE", p)
        return {"operation_id": None, "since": None, "reason": "corrupt maintenance flag", "corrupt": True}
    return raw


def is_active() -> bool:
    return status() is not None


class MaintenanceError(Exception):
    """The flag could not be written; callers must fail closed."""


def _new_flag() -> dict:
    return {
        "operation_id": uuid.uuid4().hex,
        "since": datetime.now(timezone.utc).isoformat(),
        "reason": "",
    }


def enable(reason: str = "") -> dict:
    """Activate maintenance. Idempotent and race-safe: the first writer wins.

    The flag is published with os.link (fails if it exists), so concurrent
    'on' calls all end up with a single operation_id.
    """
    current = status()
    if current is not None and not current.get("corrupt"):
        return current
    info = {**_new_flag(), "reason": reason}
    try:
        return _publish_flag(info, current)
    except OSError as exc:
        raise MaintenanceError(f"could not enable maintenance mode: {exc}") from exc


def _publish_flag(info: dict, current: Optional[dict]) -> dict:
    p = flag_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(info, f)
            f.flush()
            os.fsync(f.fileno())
        if current is None:
            try:
                os.link(tmp, p)
                return info
            except FileExistsError:
                winner = status()
                if winner is not None and not winner.get("corrupt"):
                    return winner
        os.replace(tmp, p)  # replacing a corrupt flag
        return info
    finally:
        Path(tmp).unlink(missing_ok=True)


def disable() -> bool:
    """Clear the flag (even a corrupt one). Returns True if a flag was removed."""
    try:
        flag_path().unlink()
    except FileNotFoundError:
        return False
    return True


# ── in-flight sync markers (cross-process; the sync lock is process-local) ──
# One file per running sync (sync.running.<pid>.<token>) so one finishing sync
# cannot erase another's marker. Writers create theirs BEFORE checking the
# maintenance flag; a backup that sees no fresh marker therefore knows no sync
# can have passed that check ahead of it.

SYNC_MARKER_PREFIX = "sync.running."
SYNC_STALE_SECONDS = 30 * 60


def mark_sync_running() -> Optional[Path]:
    """Create this writer's marker; returns its path, or None (logged loudly) on failure."""
    p = paths.state_dir() / f"{SYNC_MARKER_PREFIX}{os.getpid()}.{uuid.uuid4().hex[:8]}"
    try:
        write_atomic(p, f"{os.getpid()}\n")
    except OSError as exc:
        logger.error("Could not write sync marker %s (%s): backups will NOT wait for this sync", p, exc)
        return None
    return p


def clear_sync_running(marker: Optional[Path]) -> None:
    """Remove only the given (own) marker."""
    if marker is not None:
        Path(marker).unlink(missing_ok=True)


def sync_in_flight(max_age: float = SYNC_STALE_SECONDS) -> bool:
    """True when any non-stale sync marker exists."""
    now = time.time()
    for m in paths.state_dir().glob(f"{SYNC_MARKER_PREFIX}*"):
        try:
            if now - m.stat().st_mtime <= max_age:
                return True
        except FileNotFoundError:
            continue
    return False
