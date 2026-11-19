"""Retention pruning for <state>/backups.

Only direct children whose name is exactly a backup timestamp stamp, that are
real directories (never symlinks) and carry a manifest (i.e. were completed) are
ever candidates. In-progress staging dirs, foreign names and stray files are
left alone. The newest completed backup is always kept.
"""
import logging
import re
import shutil
import time
from pathlib import Path

from school_dashboard.backup import MANIFEST

logger = logging.getLogger(__name__)

DEFAULT_KEEP = 14
# Matches backup.create_backup's default: %Y%m%dT%H%M%S%fZ
_NAME_RE = re.compile(r"[0-9]{8}T[0-9]{12}Z")
# create_backup staging dir: .<stamp>.partial-<8 hex>
_PARTIAL_RE = re.compile(r"\.[0-9]{8}T[0-9]{12}Z\.partial-[0-9a-f]{8}")
PARTIAL_MAX_AGE_SECONDS = 24 * 3600


def is_backup_name(name: str) -> bool:
    return _NAME_RE.fullmatch(name) is not None


def parse_keep(raw: "str | None") -> int:
    """BACKUP_KEEP value -> positive int; unset/empty gives the default.

    Raises ValueError for anything that is not an integer >= 1 (never guesses).
    """
    if raw is None or raw.strip() == "":
        return DEFAULT_KEEP
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"BACKUP_KEEP={raw!r} is not an integer >= 1") from None
    if value < 1:
        raise ValueError(f"BACKUP_KEEP={raw!r} must be >= 1")
    return value


def _complete_backups(root: Path) -> list[Path]:
    found = []
    for entry in root.iterdir():
        if not is_backup_name(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        if (entry / MANIFEST).is_file():
            found.append(entry)
    return sorted(found, key=lambda p: p.name)


def _stale_partials(root: Path, now: float) -> list[Path]:
    found = []
    for entry in root.iterdir():
        if not _PARTIAL_RE.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        if now - entry.stat().st_mtime > PARTIAL_MAX_AGE_SECONDS:
            found.append(entry)
    return found


def prune_backups(root: Path, keep: int = DEFAULT_KEEP, now: "float | None" = None) -> list[Path]:
    """Delete all but the newest `keep` completed backups plus stale staging dirs.

    Raises ValueError (deleting nothing) when keep < 1. Returns the paths removed.
    """
    if keep < 1:
        raise ValueError(f"keep must be >= 1, got {keep}")
    root = Path(root)
    if not root.is_dir():
        return []
    doomed = _complete_backups(root)[:-keep] + _stale_partials(root, time.time() if now is None else now)
    removed = []
    for victim in doomed:
        if victim.parent != root or victim.is_symlink():  # defence in depth
            continue
        shutil.rmtree(victim)
        removed.append(victim)
        logger.info("pruned backup %s", victim.name)
    return removed
