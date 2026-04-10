# school_dashboard/digest.py
"""Three daily digest builders and ntfy.sh delivery."""
import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import requests

_log = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_state(state_path: str) -> dict:
    try:
        return json.loads(Path(state_path).read_text())
    except Exception as exc:
        _log.warning("Failed to load state from %s: %s", state_path, exc)
        return {}


def _load_facts(facts_path: str) -> list[dict]:
    try:
        p = Path(facts_path)
        if p.exists():
            return json.loads(p.read_text())
    except Exception as exc:
        _log.warning("Failed to load facts from %s: %s", facts_path, exc)
    return []


def _query_db_events(db_path: str, target_date: str) -> list[dict]:
    """Return events from the school DB on a specific ISO date."""
    if not Path(db_path).exists():
        return []
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT date, title, type, child FROM events WHERE date = ? ORDER BY title",
                (target_date,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def _call_litellm(prompt: str, litellm_url: str, api_key: str, model: str) -> str:
    """Call LiteLLM and return the reply text. Raises on failure."""
    resp = requests.post(
        f"{litellm_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 600,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected LiteLLM response shape: {data}") from exc


def _assignments_due_on(state: dict, target_date: str) -> list[dict]:
    """Return Schoology assignments with due_date matching target_date (YYYY-MM-DD)."""
    out = []
    for child, data in (state.get("schoology") or {}).items():
        for a in (data.get("assignments") or []):
            due = (a.get("due_date") or "")[:10]
            if due == target_date:
                out.append({"child": child, "title": a.get("title", ""), "course": a.get("course", "")})
    return out


def _ixl_remaining(state: dict) -> list[dict]:
    """Return per-child IXL subjects with remaining > 0."""
    out = []
    for child, data in (state.get("ixl") or {}).items():
        for subj, vals in (data.get("totals") or {}).items():
            if vals.get("remaining", 0) > 0:
                out.append({"child": child, "subject": subj, "remaining": vals["remaining"]})
    return out


def _action_items_due_on(state: dict, target_date: str) -> list[dict]:
    """Return email action items with due date matching target_date."""
    out = []
    for item in (state.get("action_items") or []):
        if item.get("source") != "email":
            continue
        due = (item.get("due") or "")[:10]
        if due == target_date:
            out.append({"child": item.get("child", ""), "summary": item.get("summary", "")})
    return out


def _gcal_events_on(gcal_events: list[dict], target_date: str) -> list[dict]:
    """Filter GCal events that start on target_date."""
    return [e for e in gcal_events if (e.get("start") or "")[:10] == target_date]


# ── Digest builders ──────────────────────────────────────────────────────────

def build_morning_digest(
    state_path: str,
    db_path: str,
    facts_path: str,
    gcal_events: list[dict],
    litellm_url: str,
    api_key: str,
    model: str,
    today: str | None = None,
) -> str:
    """Build a morning briefing: what does today hold?"""
    today = today or date.today().isoformat()
    state = _load_state(state_path)
    facts = _load_facts(facts_path)

    db_events = _query_db_events(db_path, today)
    cal_events = _gcal_events_on(gcal_events, today)
    assignments = _assignments_due_on(state, today)
    ixl = _ixl_remaining(state)
    action_items = _action_items_due_on(state, today)

    db_str = "\n".join(f"- {e['title']} ({e['type']})" for e in db_events) or "None"
    cal_str = (
        "\n".join(
            f"- {e['title']}" + (f" @ {e['location']}" if e.get("location") else "")
            for e in cal_events
        )
        or "None"
    )
    assign_str = (
        "\n".join(f"- {a['child']}: {a['title']} ({a['course']})" for a in assignments) or "None"
    )
    ixl_str = (
        "\n".join(f"- {i['child']}: {i['subject']} ({i['remaining']} remaining)" for i in ixl)
        or "All clear"
    )
    action_str = (
        "\n".join(f"- {a['child']}: {a['summary']}" for a in action_items) or "None"
    )
    facts_str = (
        "\n".join(f"- [{f.get('subject', '?')}] {f.get('fact', '')}" for f in facts[:10]) or "None"
    )

    prompt = f"""You are a family assistant sending a morning briefing push notification. Be brief, warm, and practical. Max 200 words.

Today: {today}

School calendar events today:
{db_str}

Family calendar today (Google Calendar):
{cal_str}

Assignments due today:
{assign_str}

IXL work remaining:
{ixl_str}

Action items due today:
{action_str}

Known facts (recurring activities):
{facts_str}

Write a short morning briefing: what does today hold? Mention anything urgent first."""

    return _call_litellm(prompt, litellm_url, api_key, model)


def build_afternoon_digest(
    state_path: str,
    litellm_url: str,
    api_key: str,
    model: str,
    today: str | None = None,
) -> str:
    """Build an afternoon homework check: did the kids do their work?"""
    today = today or date.today().isoformat()
    tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    state = _load_state(state_path)

    due_today = _assignments_due_on(state, today)
    due_tomorrow = _assignments_due_on(state, tomorrow)
    ixl = _ixl_remaining(state)
    action_items = _action_items_due_on(state, today)

    today_str = (
        "\n".join(f"- {a['child']}: {a['title']} ({a['course']})" for a in due_today) or "None"
    )
    tomorrow_str = (
        "\n".join(f"- {a['child']}: {a['title']} ({a['course']})" for a in due_tomorrow) or "None"
    )
    ixl_str = (
        "\n".join(f"- {i['child']}: {i['subject']} ({i['remaining']} remaining)" for i in ixl)
        or "All clear"
    )
    action_str = (
        "\n".join(f"- {a['child']}: {a['summary']}" for a in action_items) or "None"
    )

    prompt = f"""You are a family assistant sending an afternoon homework check push notification. Be direct. Max 150 words.

Today: {today}

Assignments due TODAY (should already be done or being done now):
{today_str}

Assignments due TOMORROW (should be started):
{tomorrow_str}

IXL work still remaining:
{ixl_str}

Action items due today:
{action_str}

Write a brief afternoon check-in: what homework still needs to be done? Flag anything urgent."""

    return _call_litellm(prompt, litellm_url, api_key, model)


def build_night_digest(
    state_path: str,
    db_path: str,
    facts_path: str,
    gcal_events: list[dict],
    litellm_url: str,
    api_key: str,
    model: str,
    tomorrow: str | None = None,
) -> str:
    """Build a night prep summary: what do we need ready for tomorrow?"""
    tomorrow = tomorrow or (date.today() + timedelta(days=1)).isoformat()
    state = _load_state(state_path)
    facts = _load_facts(facts_path)

    db_events = _query_db_events(db_path, tomorrow)
    cal_events = _gcal_events_on(gcal_events, tomorrow)
    assignments = _assignments_due_on(state, tomorrow)
    action_items = _action_items_due_on(state, tomorrow)

    db_str = "\n".join(f"- {e['title']} ({e['type']})" for e in db_events) or "None"
    cal_str = (
        "\n".join(
            f"- {e['title']}" + (f" @ {e['location']}" if e.get("location") else "")
            for e in cal_events
        )
        or "None"
    )
    assign_str = (
        "\n".join(f"- {a['child']}: {a['title']} ({a['course']})" for a in assignments) or "None"
    )
    action_str = (
        "\n".join(f"- {a['child']}: {a['summary']}" for a in action_items) or "None"
    )
    facts_str = (
        "\n".join(f"- [{f.get('subject', '?')}] {f.get('fact', '')}" for f in facts[:10]) or "None"
    )

    prompt = f"""You are a family assistant sending a night prep push notification. Be brief and actionable. Max 150 words.

Tomorrow: {tomorrow}

School calendar events tomorrow:
{db_str}

Family calendar tomorrow (Google Calendar):
{cal_str}

Assignments due tomorrow:
{assign_str}

Action items due tomorrow:
{action_str}

Known facts (recurring activities):
{facts_str}

Write a brief night summary: what do we need to have ready for tomorrow? Mention gear, forms, early wake-ups, or anything to prepare tonight."""

    return _call_litellm(prompt, litellm_url, api_key, model)


# ── Delivery ─────────────────────────────────────────────────────────────────

def send_ntfy(topic: str, message: str, title: str = "School") -> None:
    """Push message to ntfy.sh topic. Uses JSON body to support Unicode in title."""
    resp = requests.post(
        "https://ntfy.sh/",
        json={"topic": topic, "message": message, "title": title, "priority": "default", "tags": ["school"]},
        timeout=15,
    )
    if not resp.ok:
        _log.warning("ntfy delivery failed: %s %s", resp.status_code, resp.text[:200])
