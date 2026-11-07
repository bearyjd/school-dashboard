"""Rate-limited ntfy alert: GameChanger schedules are coming from calendar feeds only.

The ICS fallback keeps schedules flowing, which would otherwise hide a broken
API login. State lives in <state dir>/gc-fallback-alert.json; at most one alert
per 24 hours. Never raises: a failed send must not fail the scrape.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from school_dashboard import paths
from school_dashboard.atomic import write_atomic

INTERVAL = timedelta(hours=24)
TITLE = "School: GameChanger login is failing"
BODY = ("Schedules are coming from calendar links only; game opponents may be missing. "
        "The login needs attention.")


def state_file() -> Path:
    return paths.state_dir() / "gc-fallback-alert.json"


def _last_sent(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(json.loads(path.read_text())["last_sent"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _default_send(topic: str, message: str, title: str) -> None:
    from school_dashboard.watchdog import _default_send as send  # raises on non-2xx
    send(topic, message, title)


def maybe_alert(ics_teams: int, total_teams: int, now: Optional[datetime] = None,
                send: Optional[Callable[[str, str, str], None]] = None,
                path: Optional[Path] = None) -> bool:
    """Send the alert if any team used the feed and none was sent in 24h. Returns True if sent."""
    if ics_teams <= 0:
        return False
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        return False
    now = now or datetime.now(timezone.utc)
    path = path or state_file()
    last = _last_sent(path)
    if last is not None and now - last < INTERVAL:
        return False
    try:
        (send or _default_send)(topic, BODY, TITLE)
        write_atomic(path, json.dumps({"last_sent": now.isoformat(), "ics_teams": ics_teams,
                                       "total_teams": total_teams}))
    except Exception as exc:  # noqa: BLE001 - alerting must never fail the scrape
        print(f"WARN: gc fallback alert not sent: {type(exc).__name__}", file=sys.stderr)
        return False
    return True
