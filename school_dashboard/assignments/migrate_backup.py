"""Pre-migration backup: sqlite online backup + integrity check, state JSON copy with byte comparison."""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from school_dashboard.atomic import mkdir_private, write_atomic

logger = logging.getLogger(__name__)

DB_COPY_NAME = "school.db"
STATE_COPY_NAME = "school-state.json"


class BackupError(RuntimeError):
    """The backup could not be made or did not verify."""


def _new_dir(archive_root: Path, now: datetime) -> Path:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mkdir_private(archive_root)
    for n in range(100):
        target = archive_root / (f"pre-v2-{stamp}" if n == 0 else f"pre-v2-{stamp}-{n}")
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            continue
        os.chmod(target, 0o700)
        return target
    raise BackupError("could not allocate a backup directory")


def _copy_database(db_path: str, dest: Path) -> None:
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    os.chmod(dest, 0o600)
    if result != "ok":
        raise BackupError("integrity check of the database copy failed")


def _copy_state(state_path: str, dest: Path) -> None:
    data = Path(state_path).read_bytes()
    write_atomic(dest, data, 0o600)
    if dest.read_bytes() != data:
        raise BackupError("state copy is not byte-identical to the original")


def make_backup(db_path: str, state_path: str, archive_root: Path, now: datetime) -> str:
    """Create `<archive_root>/pre-v2-<UTC>/` holding a verified copy of both files; returns its path."""
    target = _new_dir(archive_root, now)
    try:
        _copy_database(db_path, target / DB_COPY_NAME)
        _copy_state(state_path, target / STATE_COPY_NAME)
    except (sqlite3.Error, OSError) as exc:
        raise BackupError(f"backup failed ({type(exc).__name__})") from exc
    logger.info("pre-migration backup written")
    return str(target)
