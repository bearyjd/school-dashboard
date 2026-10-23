"""Per-source sync metadata: read/write state/sync_meta.json."""
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from school_dashboard import maintenance, paths
from school_dashboard.atomic import write_atomic

_write_lock = threading.Lock()
logger = logging.getLogger(__name__)


def _resolve_path(path: str | None) -> str:
    return str(path or paths.sync_meta_path())


def read_sync_meta(path: str | None = None) -> dict[str, Any]:
    """Return per-source sync metadata dict. Returns {} if file missing or unreadable."""
    p = _resolve_path(path)
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def write_sync_source(source: str, result: str, path: str | None = None) -> None:
    """Write/update a single source entry in sync_meta.json."""
    p = _resolve_path(path)
    if maintenance.is_active():
        logger.info("Maintenance mode active; not recording sync result for %s", source)
        return
    with _write_lock:
        meta = read_sync_meta(p)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # A pause is not a scrape: keep the previous real scrape time if there is one.
        previous_run = (meta.get(source) or {}).get("last_run")
        meta[source] = {
            "last_run": previous_run if result == "paused" and previous_run else now,
            "last_result": result,
        }
        try:
            write_atomic(Path(p), json.dumps(meta, indent=2))
        except OSError as exc:
            logger.error("Failed to write sync meta to %s: %s", p, exc)
