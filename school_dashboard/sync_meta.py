"""Per-source sync metadata: read/write state/sync_meta.json."""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PATH = "/app/state/sync_meta.json"

_write_lock = threading.Lock()
logger = logging.getLogger(__name__)


def _resolve_path(path: str | None) -> str:
    return path or os.environ.get("SCHOOL_SYNC_META_PATH", DEFAULT_PATH)


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
    with _write_lock:
        meta = read_sync_meta(p)
        meta[source] = {
            "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_result": result,
        }
        try:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w") as f:
                json.dump(meta, f, indent=2)
        except OSError as exc:
            logger.error("Failed to write sync meta to %s: %s", p, exc)
