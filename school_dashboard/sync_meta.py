"""Per-source sync metadata: read/write state/sync_meta.json."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = "/app/state/sync_meta.json"


def _resolve_path(path: str | None) -> str:
    return path or os.environ.get("SCHOOL_SYNC_META_PATH", DEFAULT_PATH)


def read_sync_meta(path: str | None = None) -> dict:
    """Return per-source sync metadata dict. Returns {} if file missing or corrupt."""
    p = _resolve_path(path)
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_sync_source(source: str, result: str, path: str | None = None) -> None:
    """Write/update a single source entry in sync_meta.json."""
    p = _resolve_path(path)
    meta = read_sync_meta(p)
    meta[source] = {
        "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_result": result,
    }
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(meta, f, indent=2)
