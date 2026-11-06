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


def _stale(source: str, ok: datetime, now: datetime, tz: tzinfo,
           grace: timedelta) -> Optional[Problem]:
    expected = expected_sync(now, tz)
    # Only judge once the scheduled run has had time to finish.
    if now - expected >= grace and ok < expected - grace:
        return Problem(source, "stale", _hours(now - ok))
    return None


def check_source(source: str, entry: Optional[dict], now: datetime, tz: tzinfo,
                 grace: timedelta = GRACE) -> Optional[Problem]:
    ok = _parse(last_ok_time(entry))
    if ok is None:
        return Problem(source, "never_ok", None)
    if isinstance(entry, dict) and entry.get("last_result") == "error" and now - ok > FAILING_AFTER:
        return Problem(source, "failing", _hours(now - ok))
    return _stale(source, ok, now, tz, grace)


def check_mtime(name: str, mtime: Optional[datetime], now: datetime, tz: tzinfo,
                grace: timedelta = GRACE) -> Optional[Problem]:
    if mtime is None:
        return Problem(name, "never_ok", None)
    return _stale(name, mtime, now, tz, grace)


def evaluate(meta: dict, sources, now: datetime, tz: tzinfo) -> list[Problem]:
    found = (check_source(s, meta.get(s), now, tz) for s in sources)
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
        same = prev.get("issue") == p.issue and last is not None
        if same and now - last < REALERT_AFTER:
            new_state[p.source] = dict(prev)
            continue
        alerts.append(p)
        first = prev.get("first_alerted") if same and prev.get("first_alerted") else stamp
        new_state[p.source] = {"issue": p.issue, "first_alerted": first, "last_alerted": stamp}
    active = {p.source for p in problems}
    recovered = sorted(k for k, v in state.items() if k not in active and isinstance(v, dict))
    return alerts, recovered, new_state


def _label(source: str) -> str:
    return "school-state.json" if source == STATE_KEY else source


def _describe(p: Problem) -> str:
    if p.age_hours is None:
        return f"{_label(p.source)}: no successful sync on record"
    kind = "failing, last success" if p.issue == "failing" else "last success"
    return f"{_label(p.source)}: {kind} {p.age_hours:g}h ago"


def format_alert(problems: list[Problem]) -> str:
    return "\n".join(_describe(p) for p in problems)


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
    from school_dashboard.digest import send_ntfy
    send_ntfy(topic, message, title=title)


def _problem_dict(p: Problem) -> dict:
    return {"source": p.source, "issue": p.issue, "age_hours": p.age_hours}


def run(now: Optional[datetime] = None, dry_run: bool = False,
        send: Optional[SendFn] = None) -> dict:
    """Evaluate, de-duplicate, notify. Returns a result dict with `exit_code`."""
    if os.environ.get("WATCHDOG_ENABLED", "").strip().lower() in _FALSY:
        return {"exit_code": 0, "skipped": "disabled", "healthy": True, "problems": []}
    if maintenance.is_active():
        return {"exit_code": 0, "skipped": "maintenance", "healthy": True, "problems": []}
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic and not dry_run:
        logger.warning("NTFY_TOPIC unset; watchdog has nowhere to send alerts")
        return {"exit_code": 0, "skipped": "no_ntfy_topic", "healthy": True, "problems": []}

    tz = get_tz()
    now = now or datetime.now(tz)
    problems = evaluate(read_sync_meta(), monitored_sources(), now, tz)
    state_problem = check_mtime(STATE_KEY, _state_mtime(), now, tz)
    if state_problem:
        problems.append(state_problem)

    state_file = paths.state_dir() / WATCHDOG_FILE
    alerts, recovered, new_state = plan_alerts(problems, load_state(state_file), now)
    result = {
        "exit_code": 1 if problems else 0,
        "healthy": not problems,
        "problems": [_problem_dict(p) for p in problems],
        "would_alert": [_problem_dict(p) for p in alerts],
        "would_recover": recovered,
    }
    if dry_run:
        return result

    send = send or _default_send
    try:
        if alerts:
            send(topic, format_alert(alerts), ALERT_TITLE)
        if recovered:
            send(topic, format_recovered(recovered), RECOVERED_TITLE)
        save_state(state_file, new_state)
    except Exception as exc:  # delivery/write failed: leave state so the next run retries
        logger.error("watchdog notify/persist failed: %s", exc)
        result["error"] = str(exc)
    return result
