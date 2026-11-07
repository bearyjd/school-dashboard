"""Rate-limited ntfy alerts about the GameChanger calendar-feed fallback.

The ICS fallback keeps schedules flowing, which would otherwise hide a broken
API login or a broken feed config. State lives in the state dir (one file per
alert kind); at most one alert of each kind per 24 hours. These functions never
raise: a failed alert must not fail the scrape.
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
CONFIG_TITLE = "School: calendar feed config problem"
CONFIG_BODY = "The calendar feed file could not be read (check its permissions)."

Sender = Callable[[str, str, str], None]


def state_file() -> Path:
    return paths.state_dir() / "gc-fallback-alert.json"


def config_state_file() -> Path:
    return paths.state_dir() / "gc-feed-config-alert.json"


def _last_sent(path: Path, now: datetime) -> Optional[datetime]:
    """Previous send time, or None if absent, unreadable, naive or in the future."""
    try:
        last = datetime.fromisoformat(json.loads(path.read_text())["last_sent"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if last.tzinfo is None or last > now:
        return None
    return last


def _default_send(topic: str, message: str, title: str) -> None:
    from school_dashboard.watchdog import _default_send as send  # raises on non-2xx
    send(topic, message, title)


def _send_limited(title: str, body: str, path: Path, now: Optional[datetime],
                  send: Optional[Sender], extra: dict) -> bool:
    try:
        topic = os.environ.get("NTFY_TOPIC", "")
        if not topic:
            return False
        now = now or datetime.now(timezone.utc)
        last = _last_sent(path, now)
        if last is not None and now - last < INTERVAL:
            return False
        (send or _default_send)(topic, body, title)
        write_atomic(path, json.dumps({"last_sent": now.isoformat(), **extra}))
    except Exception as exc:  # noqa: BLE001 - alerting must never fail the scrape
        print(f"WARN: gc fallback alert not sent: {type(exc).__name__}", file=sys.stderr)
        return False
    return True


def maybe_alert(ics_teams: int, total_teams: int, now: Optional[datetime] = None,
                send: Optional[Sender] = None, path: Optional[Path] = None) -> bool:
    """Alert if any team used the feed and none was sent in 24h. Returns True if sent."""
    if ics_teams <= 0:
        return False
    return _send_limited(TITLE, BODY, path or state_file(), now, send,
                         {"ics_teams": ics_teams, "total_teams": total_teams})


def maybe_config_alert(now: Optional[datetime] = None, send: Optional[Sender] = None,
                       path: Optional[Path] = None) -> bool:
    """Alert (max 1/24h, own state file) that the feed config could not be read."""
    return _send_limited(CONFIG_TITLE, CONFIG_BODY, path or config_state_file(), now, send, {})
