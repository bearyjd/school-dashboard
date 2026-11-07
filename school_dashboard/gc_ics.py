"""GameChanger per-team calendar-feed (ICS) fallback.

The feed URL embeds a team-scoped secret token. It is never logged, printed or
placed in an exception message: every error raised here carries a fixed,
secret-free message (team id prefix and HTTP status at most).
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

DEFAULT_HOST = "api.team-manager.gc.com"
LOCAL_TZ = "America/New_York"
FETCH_TIMEOUT = 20
MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)


class ICSError(Exception):
    """Failure with a message that is safe to print (contains no URL/token)."""


class NoFeedConfigured(ICSError):
    pass


def _warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def feeds_path() -> Path:
    override = os.environ.get("GC_ICS_FEEDS")
    return Path(override) if override else Path.home() / ".gc" / "ics-feeds.json"


def load_feeds(path: Path | None = None) -> dict[str, str]:
    """Read {teamId: url}. Returns {} if the file does not exist."""
    p = path or feeds_path()
    try:
        st = p.stat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ICSError(f"cannot read feed config: {type(exc).__name__}") from None
    if stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != os.getuid():
        _warn(f"{p.name} should be mode 0600 and owned by the current user (found mode "
              f"{oct(stat.S_IMODE(st.st_mode))}, uid {st.st_uid})")
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        raise ICSError("feed config is not valid JSON") from None
    if not isinstance(data, dict):
        raise ICSError("feed config must be a JSON object {teamId: url}")
    return {str(k): v for k, v in data.items() if isinstance(v, str) and v}


def validate_url(team_id: str, url: str) -> str:
    """Return an https (or test-override http) URL, or raise ICSError."""
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    extra = {h.strip().lower() for h in os.environ.get("GC_ICS_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host == DEFAULT_HOST:
        if parts.scheme != "https":
            raise ICSError("feed URL must use https")
    elif host in extra:
        if parts.scheme not in ("http", "https"):
            raise ICSError("feed URL has unsupported scheme")
    else:
        raise ICSError("feed URL host is not allowed")
    if parse_qs(parts.query).get("teamId", [""])[0] != team_id:
        raise ICSError("feed URL teamId does not match its config key")
    return url


def fetch_ics(team_id: str, url: str) -> str:
    safe_url = validate_url(team_id, url)
    tag = team_id[:8]
    try:
        resp = requests.get(safe_url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT,
                            stream=True, allow_redirects=False)
        try:
            if resp.status_code != 200:
                raise ICSError(f"feed fetch for team {tag} failed: HTTP {resp.status_code}")
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_content(chunk_size=65536):
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


def _to_local(value: str, params: dict[str, str]) -> tuple[str, str]:
    """Return (YYYY-MM-DD, HH:MM) in LOCAL_TZ; time '' for all-day."""
    local = ZoneInfo(LOCAL_TZ)
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date().isoformat(), ""
    if value.endswith("Z"):
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


def _build_event(fields: list[tuple[str, dict[str, str], str]]) -> tuple[dict | None, str]:
    first: dict[str, tuple[dict[str, str], str]] = {}
    for name, params, value in fields:
        first.setdefault(name, (params, value))
    if first.get("STATUS", ({}, ""))[1].strip().upper() == "CANCELLED":
        return None, ""
    params, value = first["DTSTART"]
    day, time_ = _to_local(value, params)
    summary = _unescape(first.get("SUMMARY", ({}, ""))[1]).strip()
    location = _unescape(first.get("LOCATION", ({}, ""))[1]).replace("\n", ", ").strip()
    etype, opponent, home_away = classify(summary)
    evt = {"date": day, "time": time_, "type": etype, "opponent": opponent,
           "location": location, "home_away": home_away}
    return evt, first.get("UID", ({}, ""))[1].strip()


def parse_ics(text: str) -> tuple[list[dict], dict]:
    """Parse ICS text -> (events sorted by date/time, meta{calname, skipped})."""
    calname = ""
    skipped = 0
    by_uid: dict[str, dict] = {}
    anon: list[dict] = []
    current: list[tuple[str, dict[str, str], str]] | None = None
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
                evt, uid = _build_event(fields)
            except (ValueError, KeyError):
                skipped += 1
                continue
            if evt is None:
                continue
            if uid:
                by_uid[uid] = evt  # duplicate UID: later definition wins
            else:
                anon.append(evt)
        elif current is not None:
            current.append((name, params, value))
        elif name == "X-WR-CALNAME":
            calname = _unescape(value).strip()
    if current is not None:
        skipped += 1  # unterminated VEVENT
    events = sorted([*by_uid.values(), *anon], key=lambda e: (e["date"], e["time"]))
    return events, {"calname": calname, "skipped": skipped}


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
    return {"team_id": team_id, "team_name": meta["calname"], "source": "ics",
            "schedule": events, "warnings": warnings}
