"""Retention pruning for <state>/backups.

Only direct children whose name is exactly a backup timestamp stamp, that are
real directories (never symlinks) and carry a manifest (i.e. were completed) are
ever candidates. In-progress staging dirs, foreign names and stray files are
left alone. The newest completed backup is always kept.
"""
import logging
import re
import shutil
from pathlib import Path

from school_dashboard.backup import MANIFEST

logger = logging.getLogger(__name__)

DEFAULT_KEEP = 14
# Matches backup.create_backup's default: %Y%m%dT%H%M%S%fZ
_NAME_RE = re.compile(r"[0-9]{8}T[0-9]{12}Z")


def is_backup_name(name: str) -> bool:
    return _NAME_RE.fullmatch(name) is not None


def parse_keep(raw: "str | None") -> int:
    """BACKUP_KEEP value -> positive int; unset or invalid falls back to the default."""
    if raw is None or raw.strip() == "":
        return DEFAULT_KEEP
    try:
        value = int(raw)
    except ValueError:
        logger.warning("BACKUP_KEEP=%r is not an integer; using %d", raw, DEFAULT_KEEP)
        return DEFAULT_KEEP
    if value < 1:
        logger.warning("BACKUP_KEEP=%r must be >= 1; using %d", raw, DEFAULT_KEEP)
        return DEFAULT_KEEP
    return value


def _complete_backups(root: Path) -> list[Path]:
    found = []
    for entry in root.iterdir():
        if not is_backup_name(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        if (entry / MANIFEST).is_file():
            found.append(entry)
    return sorted(found, key=lambda p: p.name)


def prune_backups(root: Path, keep: int = DEFAULT_KEEP) -> list[Path]:
    """Delete all but the newest `keep` (min 1) completed backups; return those removed."""
    root = Path(root)
    if not root.is_dir():
        return []
    keep = max(1, keep)
    complete = _complete_backups(root)
    doomed = complete[:-keep]
    removed = []
    for victim in doomed:
        if victim.parent != root or victim.is_symlink():  # defence in depth
            continue
        shutil.rmtree(victim)
        removed.append(victim)
        logger.info("pruned backup %s", victim.name)
    return removed
