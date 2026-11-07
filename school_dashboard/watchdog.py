"""Staleness watchdog: push an ntfy alert when a data source silently stops syncing.

Pure decision functions (expected_sync, check_source, check_mtime, plan_alerts)
plus a small I/O shell (run). Reads sync_meta.json only; no HTTP, no scraping.
Alert text carries source names and ages only: no credentials, no child names.
"""
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import requests

from school_dashboard import maintenance, paths
from school_dashboard.atomic import write_atomic
from school_dashboard.sources import source_enabled
from school_dashboard.sync_meta import last_ok_time, read_sync_meta

logger = logging.getLogger(__name__)

SOURCES = ("ixl", "sgy", "gc", "powerschool")
STATE_KEY = "state"  # pseudo-source: school-state.json freshness
DEFAULT_TZ = "America/New_York"
# Mirrors the school-sync.sh entries in docker/crontab (weekdays, local time).
SYNC_SCHEDULE = ((6, 0), (14, 30))
SYNC_WEEKDAYS = (0, 1, 2, 3, 4)
GRACE = timedelta(minutes=90)
FAILING_AFTER = timedelta(hours=24)
REALERT_AFTER = timedelta(hours=24)
ALERT_TITLE = "School: data is stale"
RECOVERED_TITLE = "School: data recovered"
WATCHDOG_FILE = "watchdog.json"

_FALSY = frozenset({"0", "false", "no", "off"})

SendFn = Callable[[str, str, str], None]


@dataclass(frozen=True)
class Problem:
    source: str
    issue: str  # never_ok | failing | stale
    age_hours: Optional[float]


def get_tz() -> tzinfo:
    try:
        return ZoneInfo(os.environ.get("TZ") or DEFAULT_TZ)
    except Exception:  # unknown zone name: use the schedule's own zone
        return ZoneInfo(DEFAULT_TZ)


def expected_sync(now: datetime, tz: tzinfo) -> datetime:
    """Most recent scheduled sync time at or before `now` (aware, in `tz`)."""
    local = now.astimezone(tz)
    for back in range(0, 8):
        day = local.date() - timedelta(days=back)
        if day.weekday() not in SYNC_WEEKDAYS:
            continue
        for h, m in sorted(SYNC_SCHEDULE, reverse=True):
            candidate = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if candidate <= local:
                return candidate
    raise ValueError("no scheduled sync found in the last 8 days")


def _parse(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 1)


# Public names for other modules; the underscore names stay for existing callers.
parse_ts = _parse
hours = _hours


def _stale(source: str, ok: datetime, now: datetime, tz: tzinfo,
           grace: timedelta) -> Optional[Problem]:
    expected = expected_sync(now, tz)
    # Only judge once the scheduled run has had time to finish.
    if now - expected >= grace and ok < expected - grace:
        return Problem(source, "stale", _hours(now - ok))
    return None


def _never_ok(source: str, first_seen: Optional[datetime], now: datetime, tz: tzinfo,
              grace: timedelta) -> Optional[Problem]:
    """Alert only after a scheduled sync has had its chance (plus grace) since first_seen."""
    if first_seen is None:
        return None
    if expected_sync(now - grace, tz) > first_seen:
        return Problem(source, "never_ok", None)
    return None


def check_source(source: str, entry: Optional[dict], now: datetime, tz: tzinfo,
                 grace: timedelta = GRACE,
                 first_seen: Optional[datetime] = None) -> Optional[Problem]:
    ok = _parse(last_ok_time(entry))
    if ok is None:
        return _never_ok(source, first_seen, now, tz, grace)
    if isinstance(entry, dict) and entry.get("last_result") == "error" and now - ok > FAILING_AFTER:
        return Problem(source, "failing", _hours(now - ok))
    return _stale(source, ok, now, tz, grace)


def check_mtime(name: str, mtime: Optional[datetime], now: datetime, tz: tzinfo,
                grace: timedelta = GRACE,
                first_seen: Optional[datetime] = None) -> Optional[Problem]:
    if mtime is None:
        return _never_ok(name, first_seen, now, tz, grace)
    return _stale(name, mtime, now, tz, grace)


def evaluate(meta: dict, sources, now: datetime, tz: tzinfo,
             first_seen: dict[str, datetime]) -> list[Problem]:
    found = (check_source(s, meta.get(s), now, tz, first_seen=first_seen.get(s)) for s in sources)
    return [p for p in found if p]


# ---- alert de-duplication ----

def load_state(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_state(path: Path, state: dict) -> None:
    write_atomic(Path(path), json.dumps(state, indent=2))


def plan_alerts(problems: list[Problem], state: dict, now: datetime):
    """Return (alerts_to_send, recovered_sources, new_state)."""
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    new_state: dict = {}
    alerts: list[Problem] = []
    for p in problems:
        prev = state.get(p.source)
        prev = prev if isinstance(prev, dict) else {}
        last = _parse(prev.get("last_alerted"))
        # One key per source: the issue label (failing/stale/never_ok) may change
        # without a new alert; only 24h of silence or a recovery re-arms it.
        if last is not None and now - last < REALERT_AFTER:
            new_state[p.source] = {**prev, "issue": p.issue}
            continue
        alerts.append(p)
        first = prev.get("first_alerted") if last is not None and prev.get("first_alerted") else stamp
        new_state[p.source] = {"issue": p.issue, "first_alerted": first, "last_alerted": stamp}
    active = {p.source for p in problems}
    recovered = sorted(k for k, v in state.items() if k not in active and isinstance(v, dict))
    return alerts, recovered, new_state


_NAMES = {"sgy": "Schoology", "ixl": "IXL", "gc": "GameChanger",
          "powerschool": "PowerSchool", STATE_KEY: "school data"}
ALERT_FOOTER = "Dashboard may be showing old info."


def _label(source: str) -> str:
    return _NAMES.get(source, source)


def _describe(p: Problem) -> str:
    if p.age_hours is None:
        return f"{_label(p.source)}: no successful sync on record"
    kind = "failing, last success" if p.issue == "failing" else "last success"
    return f"{_label(p.source)}: {kind} {p.age_hours:.1f} hours ago"


def format_alert(problems: list[Problem]) -> str:
    return "\n".join([*(_describe(p) for p in problems), ALERT_FOOTER])


def format_recovered(sources: list[str]) -> str:
    return "Recovered: " + ", ".join(_label(s) for s in sources)


# ---- I/O shell ----

def _powerschool_configured() -> bool:
    from school_dashboard.powerschool import skip_reason
    return skip_reason() == ""


def _gc_configured() -> bool:
    return bool(os.environ.get("GC_TOKEN") or os.environ.get("GC_EMAIL"))


def monitored_sources() -> list[str]:
    """Enabled sources that school-sync.sh would actually run here."""
    out = []
    for s in SOURCES:
        if not source_enabled(s):
            continue
        if s == "gc" and not _gc_configured():
            continue
        if s == "powerschool" and not _powerschool_configured():
            continue
        out.append(s)
    return out


def _state_mtime() -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(paths.state_path().stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _default_send(topic: str, message: str, title: str) -> None:
    """POST to ntfy; raises on any HTTP error or timeout so state is never advanced."""
    from school_dashboard.digest import DASHBOARD_BASE
    resp = requests.post(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "default", "Tags": "school", "Click": DASHBOARD_BASE},
        timeout=15,
    )
    resp.raise_for_status()


def _problem_dict(p: Problem) -> dict:
    return {"source": p.source, "issue": p.issue, "age_hours": p.age_hours}


def _update_first_seen(stored: Any, watched: list[str], now: datetime) -> dict[str, datetime]:
    """First time each watched source was seen; sources no longer watched are dropped."""
    stored = stored if isinstance(stored, dict) else {}
    out = {}
    for name in watched:
        out[name] = _parse(stored.get(name)) or now
    return out


def load_first_seen() -> dict[str, datetime]:
    """Per-source first-seen times recorded by the watchdog ({} when none)."""
    stored = load_state(paths.state_dir() / WATCHDOG_FILE).get("first_seen")
    stored = stored if isinstance(stored, dict) else {}
    parsed = {name: _parse(ts) for name, ts in stored.items()}
    return {name: dt for name, dt in parsed.items() if dt is not None}


def _skip(reason: str, exit_code: int = 0, error: Optional[str] = None) -> dict:
    result = {"exit_code": exit_code, "skipped": reason, "healthy": exit_code == 0, "problems": []}
    if error:
        result["error"] = error
    return result


def run(now: Optional[datetime] = None, dry_run: bool = False,
        send: Optional[SendFn] = None) -> dict:
    """Evaluate, de-duplicate, notify. Returns a result dict with `exit_code`.

    Exit codes: 0 healthy/skipped, 1 problems found, 2 internal error (including
    any failure to deliver, which leaves the alert state untouched for a retry).
    """
    if os.environ.get("WATCHDOG_ENABLED", "").strip().lower() in _FALSY:
        return _skip("disabled")
    if maintenance.is_active():
        return _skip("maintenance")
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic and not dry_run:
        msg = "NTFY_TOPIC is not set; watchdog cannot send alerts"
        logger.error(msg)
        return _skip("no_ntfy_topic", exit_code=2, error=msg)

    tz = get_tz()
    now = now or datetime.now(tz)
    state_file = paths.state_dir() / WATCHDOG_FILE
    stored = load_state(state_file)
    watched = [*monitored_sources(), STATE_KEY]
    first_seen = _update_first_seen(stored.get("first_seen"), watched, now)

    problems = evaluate(read_sync_meta(), watched[:-1], now, tz, first_seen)
    state_problem = check_mtime(STATE_KEY, _state_mtime(), now, tz, first_seen=first_seen[STATE_KEY])
    if state_problem:
        problems.append(state_problem)

    old_alerts = stored.get("alerts") if isinstance(stored.get("alerts"), dict) else {}
    alerts, recovered, new_alerts = plan_alerts(problems, old_alerts, now)
    result = {
        "exit_code": 1 if problems else 0,
        "healthy": not problems,
        "problems": [_problem_dict(p) for p in problems],
        "would_alert": [_problem_dict(p) for p in alerts],
        "would_recover": recovered,
    }
    if dry_run:
        return result

    seen_out = {k: v.astimezone(timezone.utc).isoformat(timespec="seconds") for k, v in first_seen.items()}
    send = send or _default_send
    try:
        if alerts:
            send(topic, format_alert(alerts), ALERT_TITLE)
        if recovered:
            send(topic, format_recovered(recovered), RECOVERED_TITLE)
        new_state = {"alerts": new_alerts, "first_seen": seen_out}
    except Exception as exc:  # delivery failed: keep alert state so the next run retries
        logger.error("watchdog notification failed: %s", exc)
        result.update(exit_code=2, error=str(exc))
        new_state = {**stored, "first_seen": seen_out}  # the never-ok clock must keep running
    try:
        save_state(state_file, new_state)
    except OSError as exc:
        logger.error("watchdog state write failed: %s", exc)
        result.update(exit_code=2, error=str(exc))
    return result
