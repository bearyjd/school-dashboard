# school_dashboard/digest.py
"""Three daily digest builders and ntfy.sh delivery."""
import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from typing import Literal
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
                out.append({
                    "child": child,
                    "title": a.get("title", ""),
                    "course": a.get("course", ""),
                    "due_date": (a.get("due_date") or "")[:10],
                })
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


def _load_gc_events(gc_path: str | None, days: int, from_date: str | None = None) -> list[dict]:
    """Return gc events within `days` days starting from `from_date` (default: today).

    Each item: {child, team_name, date, time, type, opponent, location, home_away}
    Returns [] if file missing, unreadable, or no events in window.
    """
    if not gc_path:
        return []
    try:
        p = Path(gc_path)
        if not p.exists():
            return []
        data = json.loads(p.read_text())
    except Exception as exc:
        _log.warning("Failed to load gc schedule from %s: %s", gc_path, exc)
        return []

    start = date.fromisoformat(from_date) if from_date else date.today()
    end = start + timedelta(days=days)

    out: list[dict] = []
    for team in (data.get("teams") or []):
        child = team.get("child") or team.get("team_name", "")
        team_name = team.get("team_name", "")
        for evt in (team.get("schedule") or []):
            evt_date_str = (evt.get("date") or "")[:10]
            if not evt_date_str:
                continue
            try:
                evt_date = date.fromisoformat(evt_date_str)
            except ValueError:
                continue
            if start <= evt_date < end:
                out.append({
                    "child": child,
                    "team_name": team_name,
                    "date": evt_date_str,
                    "time": evt.get("time", ""),
                    "type": evt.get("type", ""),
                    "opponent": evt.get("opponent", ""),
                    "location": evt.get("location", ""),
                    "home_away": evt.get("home_away", ""),
                })
    return sorted(out, key=lambda e: (e["date"], e["time"]))


def _format_gc_event_line(evt: dict, relative_date: date) -> str:
    """Format a gc event as a single digest line."""
    evt_date = date.fromisoformat(evt["date"])
    delta = (evt_date - relative_date).days
    day_str = "today" if delta == 0 else "tomorrow" if delta == 1 else evt_date.strftime("%a")

    etype = (evt.get("type") or "event").capitalize()
    time_str = evt.get("time", "")

    detail_parts: list[str] = []
    if evt.get("type") == "game" and evt.get("opponent"):
        opp = f"vs. {evt['opponent']}"
        if evt.get("home_away") == "away":
            opp += ", Away"
        detail_parts.append(opp)
    if time_str:
        detail_parts.append(time_str)
    if evt.get("location"):
        detail_parts.append(f"@ {evt['location']}")

    detail = ", ".join(detail_parts)
    line = f"• {evt['child']}: {etype} {day_str}"
    if detail:
        line += f", {detail}"
    return line


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
    gc_path: str | None = None,
) -> tuple[str, list[dict]]:
    """Build a morning briefing: what does today hold?"""
    today = today or date.today().isoformat()
    state = _load_state(state_path)
    facts = _load_facts(facts_path)

    db_events = _query_db_events(db_path, today)
    cal_events = _gcal_events_on(gcal_events, today)
    assignments = _assignments_due_on(state, today)
    ixl = _ixl_remaining(state)
    action_items = _action_items_due_on(state, today)
    gc_events = _load_gc_events(gc_path, days=3, from_date=today)

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
    gc_str = (
        "\n".join(_format_gc_event_line(e, date.fromisoformat(today)) for e in gc_events)
        or "None"
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

Extracurricular events (today + 2 days):
{gc_str}

Known facts (recurring activities):
{facts_str}

Write a short morning briefing: what does today hold? Mention anything urgent first."""

    cards: list[dict] = []
    for a in assignments:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": a["due_date"], "url": "", "done": False})
    for i in ixl:
        cards.append({"source": "ixl", "child": i["child"], "title": i["subject"],
                       "detail": f"{i['remaining']} remaining", "due_date": None, "url": "", "done": False})
    for a in action_items:
        cards.append({"source": "email", "child": a["child"], "title": a["summary"],
                       "detail": "Email action item", "due_date": today, "url": "", "done": False})
    for e in db_events:
        cards.append({"source": "calendar", "child": e.get("child", ""), "title": e["title"],
                       "detail": e["type"], "due_date": today, "url": "", "done": False})
    for e in cal_events:
        cards.append({"source": "calendar", "child": "", "title": e.get("title", ""),
                       "detail": e.get("location", ""), "due_date": today, "url": "", "done": False})
    for e in gc_events:
        cards.append({"source": "gc", "child": e["child"], "title": e["team_name"],
                       "detail": f"{(e.get('type') or '').capitalize()} {e['date']} {e.get('time', '')}".strip(),
                       "due_date": e["date"], "url": "", "done": False})
    return _call_litellm(prompt, litellm_url, api_key, model), cards


def build_afternoon_digest(
    state_path: str,
    litellm_url: str,
    api_key: str,
    model: str,
    today: str | None = None,
    db_path: str | None = None,
    gc_path: str | None = None,
) -> tuple[str, list[dict]]:
    """Build an afternoon homework check: did the kids do their work?"""
    today = today or date.today().isoformat()
    tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    state = _load_state(state_path)

    due_today = _assignments_due_on(state, today)
    due_tomorrow = _assignments_due_on(state, tomorrow)
    ixl = _ixl_remaining(state)
    action_items = _action_items_due_on(state, today)
    gc_events = _load_gc_events(gc_path, days=1, from_date=today)

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
    gc_str = (
        "\n".join(_format_gc_event_line(e, date.fromisoformat(today)) for e in gc_events)
        or "None"
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

Extracurricular events today:
{gc_str}

Write a brief afternoon check-in: what homework still needs to be done? Flag anything urgent."""

    cards: list[dict] = []
    for a in due_today:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": today, "url": "", "done": False})
    for a in due_tomorrow:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": tomorrow, "url": "", "done": False})
    for i in ixl:
        cards.append({"source": "ixl", "child": i["child"], "title": i["subject"],
                       "detail": f"{i['remaining']} remaining", "due_date": None, "url": "", "done": False})
    for a in action_items:
        cards.append({"source": "email", "child": a["child"], "title": a["summary"],
                       "detail": "Email action item", "due_date": today, "url": "", "done": False})
    for e in gc_events:
        cards.append({"source": "gc", "child": e["child"], "title": e["team_name"],
                       "detail": f"{(e.get('type') or '').capitalize()} {e.get('time', '')}".strip(),
                       "due_date": e["date"], "url": "", "done": False})
    text = _call_litellm(prompt, litellm_url, api_key, model)
    if db_path:
        from school_dashboard.readiness import get_checklist, format_checklist_text
        checklist = get_checklist(state_path, db_path)
        checklist_text = format_checklist_text(checklist, prefix="Action items:")
        if checklist_text:
            text = text + "\n\n" + checklist_text
    return text, cards


def build_night_digest(
    state_path: str,
    db_path: str,
    facts_path: str,
    gcal_events: list[dict],
    litellm_url: str,
    api_key: str,
    model: str,
    tomorrow: str | None = None,
    gc_path: str | None = None,
) -> tuple[str, list[dict]]:
    """Build a night prep summary: what do we need ready for tomorrow?"""
    tomorrow = tomorrow or (date.today() + timedelta(days=1)).isoformat()
    state = _load_state(state_path)
    facts = _load_facts(facts_path)

    db_events = _query_db_events(db_path, tomorrow)
    cal_events = _gcal_events_on(gcal_events, tomorrow)
    assignments = _assignments_due_on(state, tomorrow)
    action_items = _action_items_due_on(state, tomorrow)
    gc_events = _load_gc_events(gc_path, days=1, from_date=tomorrow)

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
    gc_str = (
        "\n".join(_format_gc_event_line(e, date.fromisoformat(tomorrow)) for e in gc_events)
        or "None"
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

Extracurricular events tomorrow:
{gc_str}

Known facts (recurring activities):
{facts_str}

Write a brief night summary: what do we need to have ready for tomorrow? Mention gear, forms, early wake-ups, or anything to prepare tonight."""

    cards: list[dict] = []
    for a in assignments:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": tomorrow, "url": "", "done": False})
    for e in db_events:
        cards.append({"source": "calendar", "child": e.get("child", ""), "title": e["title"],
                       "detail": e["type"], "due_date": tomorrow, "url": "", "done": False})
    for e in cal_events:
        cards.append({"source": "calendar", "child": "", "title": e.get("title", ""),
                       "detail": e.get("location", ""), "due_date": tomorrow, "url": "", "done": False})
    for a in action_items:
        cards.append({"source": "email", "child": a["child"], "title": a["summary"],
                       "detail": "Email action item", "due_date": tomorrow, "url": "", "done": False})
    for e in gc_events:
        cards.append({"source": "gc", "child": e["child"], "title": e["team_name"],
                       "detail": f"{(e.get('type') or '').capitalize()} {e.get('time', '')}".strip(),
                       "due_date": e["date"], "url": "", "done": False})
    text = _call_litellm(prompt, litellm_url, api_key, model)
    from school_dashboard.readiness import get_checklist, format_checklist_text
    checklist = get_checklist(state_path, db_path)
    checklist_text = format_checklist_text(checklist, prefix="Before bed —")
    if checklist_text:
        text = text + "\n\n" + checklist_text
    return text, cards


def build_weekly_digest(
    mode: Literal["friday", "sunday"],
    state_path: str,
    db_path: str,
    facts_path: str,
    litellm_url: str,
    api_key: str,
    model: str,
    days_ahead: int = 7,
    today: str | None = None,
    gc_path: str | None = None,
) -> tuple[str, list[dict]]:
    """Build a weekly digest: friday=week in review, sunday=week ahead preview."""
    _today = date.fromisoformat(today) if today else date.today()
    state = _load_state(state_path)
    facts = _load_facts(facts_path)

    # Collect assignments across the next `days_ahead` days
    upcoming_assignments: list[dict] = []
    for offset in range(days_ahead):
        target = (_today + timedelta(days=offset)).isoformat()
        upcoming_assignments.extend(_assignments_due_on(state, target))

    # Collect DB events for the window
    upcoming_events: list[dict] = []
    for offset in range(3 if mode == "friday" else days_ahead):
        target = (_today + timedelta(days=offset)).isoformat()
        upcoming_events.extend(_query_db_events(db_path, target))

    ixl = _ixl_remaining(state)
    gc_events = _load_gc_events(gc_path, days=days_ahead, from_date=_today.isoformat())

    assign_str = (
        "\n".join(
            f"- {a['child']}: {a['title']} ({a['course']}) due {a.get('due_date', '')[:10]}"
            for a in upcoming_assignments
        )
        or "None"
    )
    events_str = (
        "\n".join(f"- {e['date']}: {e['title']} ({e['type']})" for e in upcoming_events)
        or "None"
    )
    ixl_str = (
        "\n".join(f"- {i['child']}: {i['subject']} ({i['remaining']} remaining)" for i in ixl)
        or "All clear"
    )
    facts_str = (
        "\n".join(f"- [{f.get('subject', '?')}] {f.get('fact', '')}" for f in facts[:10])
        or "None"
    )
    gc_str = (
        "\n".join(_format_gc_event_line(e, _today) for e in gc_events)
        or "None"
    )

    if mode == "friday":
        prompt = f"""You are writing a Friday afternoon school summary for a parent. Summarize the week: what's still outstanding per child, IXL progress, anything that needs attention over the weekend. Be concise — 3-5 bullet points per child max.

Today (Friday): {_today.isoformat()}

Outstanding assignments (due within next {days_ahead} days):
{assign_str}

School events in the next 3 days:
{events_str}

IXL remaining skills per child:
{ixl_str}

Extracurricular events next {days_ahead} days:
{gc_str}

Known facts:
{facts_str}"""
    else:
        prompt = f"""You are writing a Sunday evening school preview for a parent. Summarize what's coming up this week: assignments due with dates, school events, and IXL targets to hit. Be practical and forward-looking — help the parent plan.

Today (Sunday): {_today.isoformat()}

Assignments due in the next {days_ahead} days:
{assign_str}

School calendar events next {days_ahead} days:
{events_str}

IXL remaining skills per child:
{ixl_str}

Extracurricular events next {days_ahead} days:
{gc_str}

Known facts:
{facts_str}"""

    cards: list[dict] = []
    for a in upcoming_assignments:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": a.get("due_date") or None, "url": "", "done": False})
    for i in ixl:
        cards.append({"source": "ixl", "child": i["child"], "title": i["subject"],
                       "detail": f"{i['remaining']} remaining", "due_date": None, "url": "", "done": False})
    for e in upcoming_events:
        cards.append({"source": "calendar", "child": e.get("child", ""), "title": e["title"],
                       "detail": e["type"], "due_date": e["date"], "url": "", "done": False})
    for e in gc_events:
        cards.append({"source": "gc", "child": e["child"], "title": e["team_name"],
                       "detail": f"{(e.get('type') or '').capitalize()} {e['date']}".strip(),
                       "due_date": e["date"], "url": "", "done": False})
    return _call_litellm(prompt, litellm_url, api_key, model), cards


# ── Delivery ─────────────────────────────────────────────────────────────────

_DEEP_LINKS: dict[str, str] = {
    "Morning Briefing": "?mode=all&time=week",
    "Homework Check": "?mode=schoology&time=week",
    "Night Prep": "?mode=all&time=today",
    "Week in Review": "?mode=all&time=week",
    "Week Ahead": "?mode=all&time=week",
}

DASHBOARD_BASE = "https://school.grepon.cc"


def build_quick_check(state_path: str) -> tuple[str, list]:
    """Fast homework check — no LLM. Returns per-child IXL + SGY summary as plain text."""
    state = _load_state(state_path)
    if not state:
        return "Could not load state.", []

    children = sorted(set(
        list((state.get("ixl") or {}).keys()) +
        list((state.get("schoology") or {}).keys())
    ))
    if not children:
        return "No children found in state.", []

    lines = []
    for child in children:
        ixl_totals = ((state.get("ixl") or {}).get(child) or {}).get("totals") or {}
        sgy_assignments = ((state.get("schoology") or {}).get(child) or {}).get("assignments") or []

        ixl_remaining = sum(v.get("remaining", 0) for v in ixl_totals.values())
        open_sgy = [
            a for a in sgy_assignments
            if (a.get("status") or "").lower() not in (
                "submitted", "graded", "complete", "completed", "turned in"
            )
        ]

        if ixl_remaining > 0:
            subjects = [s for s, v in ixl_totals.items() if v.get("remaining", 0) > 0]
            ixl_part = f"IXL {ixl_remaining} remaining ({', '.join(subjects)})"
        else:
            ixl_part = "IXL all done"

        sgy_part = f"SGY {len(open_sgy)} open" if open_sgy else "SGY all done"
        lines.append(f"{child}: {ixl_part}, {sgy_part}")

    return "\n".join(lines), []


def _format_ntfy_action(action: dict) -> str:
    parts = [action.get("action", "http"), action["label"], action["url"]]
    if action.get("method"):
        parts.append(f"method={action['method']}")
    if action.get("body"):
        parts.append(f"body={action['body']}")
    for k, v in (action.get("headers") or {}).items():
        parts.append(f"headers.{k}={v}")
    return ", ".join(parts)


def send_ntfy(
    topic: str,
    message: str,
    title: str = "School",
    cards: list[dict] | None = None,
    db_path: str | None = None,
    actions: list[dict] | None = None,
) -> None:
    """Push message to ntfy.sh topic. If cards provided, store digest and deep-link to carousel."""
    if cards and db_path:
        try:
            from school_dashboard.db import init_digests_table, create_digest, purge_old_digests
            init_digests_table(db_path)
            purge_old_digests(db_path, days=7)
            digest_id = create_digest(db_path, title, cards)
            url = f"{DASHBOARD_BASE}/?digest={digest_id}"
        except Exception as exc:
            _log.warning("Digest DB error, falling back to static link: %s", exc)
            deep = _DEEP_LINKS.get(title, "")
            url = f"{DASHBOARD_BASE}/{deep}" if deep else DASHBOARD_BASE
    else:
        deep = _DEEP_LINKS.get(title, "")
        url = f"{DASHBOARD_BASE}/{deep}" if deep else DASHBOARD_BASE

    base_action = f"view, Open Dashboard, {url}"
    if actions:
        extra = "; ".join(_format_ntfy_action(a) for a in actions)
        actions_header = f"{base_action}; {extra}"
    else:
        actions_header = base_action

    resp = requests.post(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={
            "Title": title.encode("ascii", errors="replace").decode("ascii"),
            "Priority": "default",
            "Tags": "school",
            "Click": url,
            "Actions": actions_header,
        },
        timeout=15,
    )
    if not resp.ok:
        _log.warning("ntfy delivery failed: %s %s", resp.status_code, resp.text[:200])
