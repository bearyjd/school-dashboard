"""GameChanger per-team calendar-feed (ICS) fallback.

The feed URL embeds a team-scoped secret token. It is never logged, printed or
placed in an exception message: every error raised here carries a fixed,
secret-free message (team id prefix and HTTP status at most).
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

from school_dashboard.powerschool import _clean_text

# urllib3 logs full request URLs (including the token) at DEBUG.
logging.getLogger("urllib3").setLevel(logging.WARNING)

DEFAULT_HOST = "api.team-manager.gc.com"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOCAL_TZ = "America/New_York"
FETCH_TIMEOUT = 20
FETCH_DEADLINE = 60
MAX_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 500
MAX_CLASSIFY_CHARS = 200
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)


class ICSError(Exception):
    """Failure with a message that is safe to print (contains no URL/token)."""


class NoFeedConfigured(ICSError):
    pass


def feeds_path() -> Path:
    override = os.environ.get("GC_ICS_FEEDS")
    return Path(override) if override else Path.home() / ".gc" / "ics-feeds.json"


def load_feeds(path: Path | None = None) -> dict[str, str]:
    """Read {teamId: url}. Returns {} if the file does not exist; refuses loose permissions."""
    p = path or feeds_path()
    try:
        st = p.stat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ICSError(f"cannot read feed config: {type(exc).__name__}") from None
    if stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != os.getuid():
        raise ICSError("feed config is readable by other users or owned by another user; "
                       "run chmod 600 on it and make sure you own it")
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        raise ICSError("feed config is not valid JSON") from None
    if not isinstance(data, dict):
        raise ICSError("feed config must be a JSON object {teamId: url}")
    return {str(k): v for k, v in data.items() if isinstance(v, str) and v}


def validate_url(team_id: str, url: str) -> str:
    """Return the exact URL that will be requested, or raise ICSError.

    The allowlist is checked against the parse of the *prepared* URL (what
    requests/urllib3 actually connect to), not a second parser's view of it.
    """
    if any(ord(c) <= 0x20 or ord(c) >= 0x7F or c == "\\" for c in url):
        raise ICSError("feed URL contains forbidden characters")
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    try:
        prepared = requests.Request("GET", url).prepare().url or ""
        parsed = parse_url(prepared)
    except (requests.RequestException, LocationParseError, ValueError):
        raise ICSError("feed URL is not valid") from None
    if parsed.auth:
        raise ICSError("feed URL must not contain credentials")
    host = (parsed.host or "").lower().strip("[]")
    scheme = (parsed.scheme or "").lower()
    extra = {h.strip().lower() for h in os.environ.get("GC_ICS_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host == DEFAULT_HOST:
        if scheme != "https" or parsed.port not in (None, 443):
            raise ICSError("feed URL must be plain https on port 443")
    elif host in extra and host in LOOPBACK_HOSTS:
        if scheme not in ("http", "https"):
            raise ICSError("feed URL has unsupported scheme")
    else:
        raise ICSError("feed URL host is not allowed")
    if parse_qs(parsed.query or "").get("teamId", [""])[0] != team_id:
        raise ICSError("feed URL teamId does not match its config key")
    return prepared


def fetch_ics(team_id: str, url: str) -> str:
    safe_url = validate_url(team_id, url)
    tag = team_id[:8]
    deadline = time.monotonic() + FETCH_DEADLINE
    try:
        resp = requests.get(safe_url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT,
                            stream=True, allow_redirects=False)
        try:
            if resp.status_code != 200:
                raise ICSError(f"feed fetch for team {tag} failed: HTTP {resp.status_code}")
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if time.monotonic() > deadline:
                    raise ICSError(f"feed fetch for team {tag} exceeded its deadline")
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ICSError(f"feed for team {tag} exceeds {MAX_BYTES} bytes")
                chunks.append(chunk)
        finally:
            resp.close()
    except ICSError:
        raise
    except requests.RequestException as exc:
        # str(exc) may embed the URL; report the class name only.
        raise ICSError(f"feed fetch for team {tag} failed: {type(exc).__name__}") from None
    return b"".join(chunks).decode("utf-8", errors="replace")


# --- parsing -----------------------------------------------------------

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _text(value: str) -> str:
    """Unescape, then flatten control chars and cap at 160 chars (untrusted feed text)."""
    return _clean_text(_unescape(value)) or ""


def _split_line(line: str) -> tuple[str, dict[str, str], str] | None:
    head, sep, value = line.partition(":")
    if not sep:
        return None
    name, *param_items = head.split(";")
    params = {}
    for item in param_items:
        k, _, v = item.partition("=")
        params[k.upper()] = v.strip('"')
    return name.upper(), params, value


def _local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(LOCAL_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        raise ICSError("timezone data for America/New_York is unavailable") from None


def _to_local(value: str, params: dict[str, str], local: ZoneInfo) -> tuple[str, str]:
    """Return (YYYY-MM-DD, HH:MM) in local time; time '' for all-day.

    A multi-day all-day event keeps its start date only.
    """
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date().isoformat(), ""
    if value.endswith("Z"):
        if not re.fullmatch(r"\d{8}T\d{6}Z", value):
            raise ValueError("bad UTC datetime")
        dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    else:
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
        tzid = params.get("TZID")
        try:
            tz = ZoneInfo(tzid) if tzid else local
        except (ZoneInfoNotFoundError, ValueError):
            tz = local
        dt = dt.replace(tzinfo=tz)
    dt = dt.astimezone(local)
    return dt.date().isoformat(), dt.strftime("%H:%M")


_VS = re.compile(r"\s+(?:vs\.?|v\.?)\s+(.+)$", re.IGNORECASE)
_AT = re.compile(r"\s+@\s+(.+)$")


def classify(summary: str) -> tuple[str, str, str]:
    """Return (type, opponent, home_away) from a SUMMARY heuristic.

    Mirrors the API path, where opponent falls back to the event title.
    """
    summary = summary[:MAX_CLASSIFY_CHARS]
    low = summary.lower()
    if "practice" in low:
        return "practice", summary, ""
    at = _AT.search(summary)
    if at:
        return "game", at.group(1).strip(), "away"
    vs = _VS.search(summary)
    if vs:
        return "game", vs.group(1).strip(), "home"
    if any(w in low for w in ("game", "scrimmage", "tournament")):
        return "game", summary, ""
    return "other", summary, ""


_Fields = list[tuple[str, dict[str, str], str]]


def _build_event(fields: _Fields, local: ZoneInfo) -> tuple[dict | None, tuple[str, str], bool]:
    """Return (event or None if cancelled, (uid, recurrence-id), is_recurring)."""
    first: dict[str, tuple[dict[str, str], str]] = {}
    for name, params, value in fields:
        first.setdefault(name, (params, value))
    key = (first.get("UID", ({}, ""))[1].strip(), first.get("RECURRENCE-ID", ({}, ""))[1].strip())
    recurring = any(n in first for n in ("RRULE", "RDATE", "EXDATE"))
    if first.get("STATUS", ({}, ""))[1].strip().upper() == "CANCELLED":
        return None, key, recurring
    params, value = first["DTSTART"]
    day, time_ = _to_local(value, params, local)
    summary = _text(first.get("SUMMARY", ({}, ""))[1])
    if not summary:
        raise ValueError("empty summary")
    location = _text(first.get("LOCATION", ({}, ""))[1].replace("\\n", ", ").replace("\\N", ", "))
    etype, opponent, home_away = classify(summary)
    evt = {"date": day, "time": time_, "type": etype, "opponent": opponent,
           "location": location, "home_away": home_away}
    return evt, key, recurring


def parse_ics(text: str) -> tuple[list[dict], dict]:
    """Parse ICS text -> (events sorted by date/time, meta)."""
    local = _local_tz()
    calname = ""
    skipped = recurring_count = 0
    by_key: dict[tuple[str, str], dict] = {}
    anon: list[dict] = []
    current: _Fields | None = None
    for line in _unfold(text):
        parsed = _split_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "BEGIN" and value.strip().upper() == "VEVENT":
            current = []
        elif name == "END" and value.strip().upper() == "VEVENT" and current is not None:
            fields, current = current, None
            try:
                evt, key, recurring = _build_event(fields, local)
            except (ValueError, KeyError, OverflowError):
                skipped += 1
                continue
            recurring_count += recurring
            if evt is None:
                by_key.pop(key, None)  # a cancellation removes an earlier same-key entry
            elif key[0]:
                by_key[key] = evt  # duplicate key: later definition wins
            else:
                anon.append(evt)
        elif current is not None:
            current.append((name, params, value))
        elif name == "X-WR-CALNAME":
            calname = _text(value)
    if current is not None:
        skipped += 1  # unterminated VEVENT
    events = sorted([*by_key.values(), *anon], key=lambda e: (e["date"], e["time"]))
    truncated = max(0, len(events) - MAX_EVENTS)
    return events[:MAX_EVENTS], {"calname": calname, "skipped": skipped,
                                 "recurring": recurring_count, "truncated": truncated}


def fetch_team_schedule(team_id: str, feeds: dict[str, str] | None = None) -> dict:
    """Fetch+parse one team's feed. Raises NoFeedConfigured / ICSError."""
    feeds = load_feeds() if feeds is None else feeds
    url = feeds.get(team_id)
    if not url:
        raise NoFeedConfigured(f"no calendar feed configured for team {team_id[:8]}")
    events, meta = parse_ics(fetch_ics(team_id, url))
    warnings = []
    if meta["skipped"]:
        warnings.append(f"skipped {meta['skipped']} malformed event(s)")
    if meta["recurring"]:
        warnings.append(f"{meta['recurring']} recurring event(s) not expanded (only first occurrence listed)")
    if meta["truncated"]:
        warnings.append(f"feed truncated to {MAX_EVENTS} events ({meta['truncated']} dropped)")
    return {"team_id": team_id, "team_name": meta["calname"], "source": "ics",
            "schedule": events, "warnings": warnings}
