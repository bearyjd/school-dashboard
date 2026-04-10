# school_dashboard/gcal.py
"""Shared Google Calendar fetch via gog CLI with in-process cache."""
import json
import logging
import os
import subprocess
import threading
import time
from datetime import date, timedelta

_log = logging.getLogger(__name__)

_cache: dict = {"data": None, "ts": 0.0}
_TTL = 900  # 15 minutes
_cache_lock = threading.Lock()


def fetch_gcal_events(gog_account: str, days: int = 30) -> list[dict]:
    """Fetch upcoming events from Google Calendar via gog CLI.

    Returns a list of event dicts with keys:
        title, start (ISO string), end (ISO string), all_day (bool),
        location, description, url (htmlLink)

    Results are cached for 15 minutes. Returns [] if gog_account is empty
    or gog exits non-zero. Falls back to cached data on exception.
    """
    if not gog_account:
        return []

    with _cache_lock:
        if _cache["data"] is not None and (time.time() - _cache["ts"]) < _TTL:
            return _cache["data"]

    try:
        end_date = (date.today() + timedelta(days=days)).isoformat()
        _extra_env: dict[str, str] = {}
        if "GOG_KEYRING_PASSWORD" in os.environ:
            _extra_env["GOG_KEYRING_PASSWORD"] = os.environ["GOG_KEYRING_PASSWORD"]
        result = subprocess.run(
            [
                "gog", "calendar", "events",
                "--from", "today",
                "--to", end_date,
                "-a", gog_account,
                "-j",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ, **_extra_env},
        )
        if result.returncode != 0:
            return _cache["data"] or []
        raw = json.loads(result.stdout)
        events: list = raw.get("events") or (raw if isinstance(raw, list) else [])
        out = []
        for e in events:
            start = e.get("start", {})
            end_val = e.get("end", {})
            out.append({
                "title": e.get("summary", ""),
                "start": start.get("dateTime") or start.get("date", ""),
                "end": end_val.get("dateTime") or end_val.get("date", ""),
                "all_day": "dateTime" not in start,
                "location": e.get("location", ""),
                "description": (e.get("description") or "")[:200],
                "url": e.get("htmlLink", ""),
            })
        with _cache_lock:
            _cache["data"] = out
            _cache["ts"] = time.time()
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("gcal fetch failed, using cached data: %s", exc)
        return _cache["data"] or []
