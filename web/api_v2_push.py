"""Fire-and-forget push sender for /api/v2: never on the request thread, at most one sender at a time."""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from school_dashboard.assignments import flagsenv, notify, schema

logger = logging.getLogger(__name__)
_busy = threading.Lock()


def _run(db_file: str, now: datetime) -> None:
    try:
        conn = schema.connect(db_file)
        try:
            notify.send_pending(conn, now=now)
        finally:
            conn.close()
    except Exception as exc:  # best effort: the write already succeeded
        logger.error("api v2: push failed (%s)", type(exc).__name__)
    finally:
        _busy.release()


def kick(db_file: str, now: datetime) -> None:
    """Start the sender unless push is off or one is already running. Never raises."""
    if not flagsenv.enabled(notify.PUSH_FLAG) or not _busy.acquire(blocking=False):
        return
    try:
        threading.Thread(target=_run, args=(db_file, now), daemon=True).start()
    except Exception as exc:
        _busy.release()
        logger.error("api v2: push thread not started (%s)", type(exc).__name__)


def wait_idle(timeout: float) -> bool:
    """True once no sender is running (used by tests and shutdown)."""
    if _busy.acquire(timeout=timeout):
        _busy.release()
        return True
    return False
