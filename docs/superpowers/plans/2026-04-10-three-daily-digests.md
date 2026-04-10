# Three Daily Digests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three timed push notifications (morning briefing, afternoon homework check, night prep) plus fix font sizes to be explicitly readable on a phone.

**Architecture:** Create `school_dashboard/gcal.py` as a shared GCal fetch module, create `school_dashboard/digest.py` with three digest-building functions and ntfy delivery, wire them into the CLI and crontab. Font sizes fixed with explicit px values per CSS class rather than global bumps.

**Tech Stack:** Python 3.12, Flask, requests, SQLite, ntfy.sh push API, LiteLLM proxy, gog CLI for GCal

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `school_dashboard/gcal.py` | Create | Shared GCal fetch with 15-min cache |
| `school_dashboard/digest.py` | Create | `build_morning_digest`, `build_afternoon_digest`, `build_night_digest`, `send_ntfy` |
| `school_dashboard/cli.py` | Modify | Add `digest` subcommand with `--mode morning/afternoon/night` |
| `web/app.py` | Modify | Replace inline GCal code with import from `gcal.py` |
| `docker/crontab` | Modify | Add three new cron entries |
| `web/templates/index.html` | Modify | Fix CSS font sizes with explicit values |
| `tests/test_digest.py` | Create | Unit tests for digest functions |

---

## Task 1: Create `school_dashboard/gcal.py`

**Files:**
- Create: `school_dashboard/gcal.py`

- [ ] **Step 1: Create the file**

```python
# school_dashboard/gcal.py
"""Shared Google Calendar fetch via gog CLI with in-process cache."""
import json
import os
import subprocess
import time
from datetime import date, timedelta

_cache: dict = {"data": None, "ts": 0.0}
_TTL = 900  # 15 minutes


def fetch_gcal_events(gog_account: str, days: int = 30) -> list[dict]:
    """Fetch upcoming events from Google Calendar via gog CLI.

    Returns a list of event dicts with keys:
        title, start (ISO string), end (ISO string), all_day (bool),
        location, description, url (htmlLink)

    Results are cached for 15 minutes. Returns [] if gog_account is empty
    or gog exits non-zero. Falls back to cached data on exception.
    """
    global _cache
    if _cache["data"] is not None and (time.time() - _cache["ts"]) < _TTL:
        return _cache["data"]
    if not gog_account:
        return []
    try:
        end_date = (date.today() + timedelta(days=days)).isoformat()
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
            env={**os.environ, "GOG_KEYRING_PASSWORD": os.environ.get("GOG_KEYRING_PASSWORD", "")},
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
        _cache = {"data": out, "ts": time.time()}
        return out
    except Exception:
        return _cache["data"] or []
```

- [ ] **Step 2: Commit**

```bash
git add school_dashboard/gcal.py
git commit -m "feat: add shared gcal.py module for GCal event fetch"
```

---

## Task 2: Update `web/app.py` to use `gcal.py`

**Files:**
- Modify: `web/app.py`

The current `app.py` has `_fetch_gcal_events()` defined inline starting around line 326. Replace it with an import.

- [ ] **Step 1: Remove the inline `_gcal_cache`, `GCAL_TTL`, and `_fetch_gcal_events` from `app.py`**

Remove these lines from `app.py`:
```python
_gcal_cache: dict = {"data": None, "ts": 0}
GCAL_TTL = 900  # 15 minutes
```

And remove the entire `_fetch_gcal_events()` function definition (~35 lines).

- [ ] **Step 2: Add import and update the `/api/calendar` route**

At the top of `app.py`, add to imports:
```python
from school_dashboard.gcal import fetch_gcal_events
```

Update the `/api/calendar` route to use the shared function:
```python
@app.route("/api/calendar")
def api_calendar():
    events = fetch_gcal_events(GOG_ACCOUNT)
    return jsonify({"events": events})
```

- [ ] **Step 3: Verify the app still starts**

```bash
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
python3 -c "from web.app import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add web/app.py
git commit -m "refactor: use shared gcal.py in Flask app"
```

---

## Task 3: Create `school_dashboard/digest.py`

**Files:**
- Create: `school_dashboard/digest.py`
- Create: `tests/test_digest.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_digest.py`:

```python
"""Tests for school_dashboard.digest"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from school_dashboard.digest import (
    build_morning_digest,
    build_afternoon_digest,
    build_night_digest,
    send_ntfy,
    _load_state,
    _load_facts,
)


@pytest.fixture
def tmp_state(tmp_path):
    state = {
        "schoology": {
            "Jack": {
                "assignments": [
                    {"title": "Math HW", "due_date": "2026-04-10", "course": "Math", "status": ""},
                    {"title": "Future HW", "due_date": "2026-04-11", "course": "ELA", "status": ""},
                ]
            }
        },
        "ixl": {
            "Jack": {"totals": {"Math": {"remaining": 3, "assigned": 5, "done": 2}}}
        },
        "action_items": [
            {"child": "Jack", "source": "email", "summary": "Return field trip form", "due": "2026-04-10"},
        ],
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state))
    return str(p)


@pytest.fixture
def tmp_facts(tmp_path):
    facts = [{"subject": "Jack", "fact": "Soccer practice on Tuesdays"}]
    p = tmp_path / "facts.json"
    p.write_text(json.dumps(facts))
    return str(p)


@pytest.fixture
def tmp_db(tmp_path):
    import sqlite3
    db = tmp_path / "school.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            date TEXT,
            title TEXT,
            type TEXT,
            child TEXT
        )
    """)
    conn.execute("INSERT INTO events VALUES (1, '2026-04-10', 'Mass', 'MASS', '')")
    conn.execute("INSERT INTO events VALUES (2, '2026-04-11', 'No School', 'NO_SCHOOL', '')")
    conn.commit()
    conn.close()
    return str(db)


def test_load_state(tmp_state):
    state = _load_state(tmp_state)
    assert "schoology" in state
    assert "Jack" in state["schoology"]


def test_load_facts(tmp_facts):
    facts = _load_facts(tmp_facts)
    assert facts[0]["fact"] == "Soccer practice on Tuesdays"


def test_load_facts_missing():
    facts = _load_facts("/nonexistent/facts.json")
    assert facts == []


@patch("school_dashboard.digest.requests.post")
def test_send_ntfy(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    send_ntfy(topic="test-topic", message="Hello", title="Test")
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "test-topic" in call_kwargs[0][0]


@patch("school_dashboard.digest.requests.post")
def test_build_morning_digest_calls_litellm(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Good morning!"}}]},
    )
    result = build_morning_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        gcal_events=[],
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        today="2026-04-10",
    )
    assert "Good morning!" in result
    mock_post.assert_called_once()


@patch("school_dashboard.digest.requests.post")
def test_build_afternoon_digest_calls_litellm(mock_post, tmp_state):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Homework check!"}}]},
    )
    result = build_afternoon_digest(
        state_path=tmp_state,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        today="2026-04-10",
    )
    assert "Homework check!" in result


@patch("school_dashboard.digest.requests.post")
def test_build_night_digest_calls_litellm(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Ready for tomorrow!"}}]},
    )
    result = build_night_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        gcal_events=[],
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        tomorrow="2026-04-11",
    )
    assert "Ready for tomorrow!" in result
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_digest.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` — `digest` module does not exist yet.

- [ ] **Step 3: Create `school_dashboard/digest.py`**

```python
# school_dashboard/digest.py
"""Three daily digest builders and ntfy.sh delivery."""
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_state(state_path: str) -> dict:
    try:
        return json.loads(Path(state_path).read_text())
    except Exception:
        return {}


def _load_facts(facts_path: str) -> list[dict]:
    try:
        p = Path(facts_path)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return []


def _query_db_events(db_path: str, target_date: str) -> list[dict]:
    """Return events from the school DB on a specific ISO date."""
    if not Path(db_path).exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT date, title, type, child FROM events WHERE date = ? ORDER BY title",
            (target_date,),
        ).fetchall()
        conn.close()
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
    return resp.json()["choices"][0]["message"]["content"]


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
    today: Optional[str] = None,
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
    cal_str = "\n".join(f"- {e['title']}" + (f" @ {e['location']}" if e.get("location") else "") for e in cal_events) or "None"
    assign_str = "\n".join(f"- {a['child']}: {a['title']} ({a['course']})" for a in assignments) or "None"
    ixl_str = "\n".join(f"- {i['child']}: {i['subject']} ({i['remaining']} remaining)" for i in ixl) or "All clear"
    action_str = "\n".join(f"- {a['child']}: {a['summary']}" for a in action_items) or "None"
    facts_str = "\n".join(f"- [{f.get('subject','?')}] {f.get('fact','')}" for f in facts[:10]) or "None"

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
    today: Optional[str] = None,
) -> str:
    """Build an afternoon homework check: did the kids do their work?"""
    today = today or date.today().isoformat()
    tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    state = _load_state(state_path)

    due_today = _assignments_due_on(state, today)
    due_tomorrow = _assignments_due_on(state, tomorrow)
    ixl = _ixl_remaining(state)
    action_items = _action_items_due_on(state, today)

    today_str = "\n".join(f"- {a['child']}: {a['title']} ({a['course']})" for a in due_today) or "None"
    tomorrow_str = "\n".join(f"- {a['child']}: {a['title']} ({a['course']})" for a in due_tomorrow) or "None"
    ixl_str = "\n".join(f"- {i['child']}: {i['subject']} ({i['remaining']} remaining)" for i in ixl) or "All clear"
    action_str = "\n".join(f"- {a['child']}: {a['summary']}" for a in action_items) or "None"

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
    tomorrow: Optional[str] = None,
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
    cal_str = "\n".join(f"- {e['title']}" + (f" @ {e['location']}" if e.get("location") else "") for e in cal_events) or "None"
    assign_str = "\n".join(f"- {a['child']}: {a['title']} ({a['course']})" for a in assignments) or "None"
    action_str = "\n".join(f"- {a['child']}: {a['summary']}" for a in action_items) or "None"
    facts_str = "\n".join(f"- [{f.get('subject','?')}] {f.get('fact','')}" for f in facts[:10]) or "None"

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
    """Push message to ntfy.sh topic."""
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "default", "Tags": "school"},
        timeout=15,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_digest.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add school_dashboard/digest.py tests/test_digest.py
git commit -m "feat: add digest.py with morning/afternoon/night builders and ntfy delivery"
```

---

## Task 4: Add `digest` subcommand to `school_dashboard/cli.py`

**Files:**
- Modify: `school_dashboard/cli.py`

- [ ] **Step 1: Add the import at the top of cli.py**

Add after the existing imports:
```python
from school_dashboard import digest as _digest
from school_dashboard.gcal import fetch_gcal_events
```

- [ ] **Step 2: Add the `cmd_digest` function**

Add before the `main()` function:
```python
def cmd_digest(args: argparse.Namespace) -> None:
    import os

    litellm_url = os.environ.get("LITELLM_URL", "")
    api_key = os.environ.get("LITELLM_API_KEY", "")
    model = os.environ.get("LITELLM_MODEL", "claude-sonnet")
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")
    db_path = os.environ.get("SCHOOL_DB_PATH", "/app/state/school.db")
    facts_path = os.environ.get("SCHOOL_FACTS_PATH", "/app/state/facts.json")
    gog_account = os.environ.get("GOG_ACCOUNT", "")
    state_file = args.state_file or os.environ.get("SCHOOL_STATE_PATH", "/app/state/school-state.json")

    if not litellm_url:
        print("Error: LITELLM_URL not set", file=sys.stderr)
        sys.exit(1)
    if not ntfy_topic:
        print("Error: NTFY_TOPIC not set", file=sys.stderr)
        sys.exit(1)

    gcal_events = fetch_gcal_events(gog_account) if gog_account else []

    if args.mode == "morning":
        text = _digest.build_morning_digest(
            state_path=state_file,
            db_path=db_path,
            facts_path=facts_path,
            gcal_events=gcal_events,
            litellm_url=litellm_url,
            api_key=api_key,
            model=model,
        )
        _digest.send_ntfy(topic=ntfy_topic, message=text, title="☀️ Today")

    elif args.mode == "afternoon":
        text = _digest.build_afternoon_digest(
            state_path=state_file,
            litellm_url=litellm_url,
            api_key=api_key,
            model=model,
        )
        _digest.send_ntfy(topic=ntfy_topic, message=text, title="📚 Homework Check")

    elif args.mode == "night":
        text = _digest.build_night_digest(
            state_path=state_file,
            db_path=db_path,
            facts_path=facts_path,
            gcal_events=gcal_events,
            litellm_url=litellm_url,
            api_key=api_key,
            model=model,
        )
        _digest.send_ntfy(topic=ntfy_topic, message=text, title="🌙 Tomorrow")

    print(f"Digest sent [{args.mode}]: {text[:80]}...", file=sys.stderr)
```

- [ ] **Step 3: Register the subcommand in `main()`**

Add before `args = parser.parse_args()`:
```python
    p_digest = subs.add_parser("digest", help="Build and send a timed digest notification")
    p_digest.add_argument(
        "--mode",
        choices=["morning", "afternoon", "night"],
        required=True,
        help="Which digest to send",
    )
    p_digest.set_defaults(func=cmd_digest)
```

- [ ] **Step 4: Verify CLI works**

```bash
python3 -m school_dashboard.cli digest --help
```

Expected output includes:
```
usage: school-state digest [-h] --mode {morning,afternoon,night}
```

- [ ] **Step 5: Commit**

```bash
git add school_dashboard/cli.py
git commit -m "feat: add digest subcommand to school-state CLI"
```

---

## Task 5: Update `docker/crontab`

**Files:**
- Modify: `docker/crontab`

- [ ] **Step 1: Add the three new cron entries**

Current `docker/crontab`:
```
# school-dashboard cron
# Set env file path so school-sync.sh finds Docker secrets
SCHOOL_DASHBOARD_ENV=/app/config/env

# Morning sync + digest at 6:00am weekdays
0 6 * * 1-5 root bash /app/sync/school-sync.sh >> /tmp/school-sync.log 2>&1
# Afternoon data refresh at 2:30pm weekdays
30 14 * * 1-5 root bash /app/sync/school-sync.sh >> /tmp/school-sync.log 2>&1
```

Replace with:
```
# school-dashboard cron
# Set env file path so school-sync.sh finds Docker secrets
SCHOOL_DASHBOARD_ENV=/app/config/env

# Morning sync at 6:00am weekdays (data refresh only)
0 6 * * 1-5 root bash /app/sync/school-sync.sh >> /tmp/school-sync.log 2>&1
# Afternoon data refresh at 2:30pm weekdays
30 14 * * 1-5 root bash /app/sync/school-sync.sh >> /tmp/school-sync.log 2>&1

# Morning digest — 7:00am daily (7 days/week)
0 7 * * * root set -a && source /app/config/env && set +a && school-state digest --mode morning >> /tmp/digest.log 2>&1
# Afternoon homework check — 3:30pm weekdays only
30 15 * * 1-5 root set -a && source /app/config/env && set +a && school-state digest --mode afternoon >> /tmp/digest.log 2>&1
# Night prep — 8:30pm daily (7 days/week)
30 20 * * * root set -a && source /app/config/env && set +a && school-state digest --mode night >> /tmp/digest.log 2>&1
```

- [ ] **Step 2: Run all tests to confirm nothing is broken**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add docker/crontab
git commit -m "feat: add morning/afternoon/night digest cron entries"
```

---

## Task 6: Fix font sizes in `web/templates/index.html`

**Files:**
- Modify: `web/templates/index.html`

The CSS currently has accumulated arbitrary px sizes from repeated global bumps. This task replaces them with explicit, intentional values optimized for phone readability. Do NOT use replace_all — edit each CSS class rule precisely.

- [ ] **Step 1: Set explicit font sizes for each CSS class**

Find and update each of these CSS rules to exactly these values:

```css
html,body { font-size: 18px; }

/* Tab bar */
.tabbar button { font-size: 18px; }

/* Chat messages */
.msg { font-size: 18px; }

/* Dashboard mode tabs (Schoology / IXL / Email / Calendar) */
.dtab { font-size: 16px; }

/* Child filter chips */
.dchip { font-size: 15px; }

/* Section headers (ALL CAPS labels above groups) */
.dash-section { font-size: 13px; }

/* Primary item text — most important, must be readable at a glance */
.dash-summary { font-size: 20px; }

/* Secondary meta line under each item */
.dash-meta { font-size: 15px; }

/* Child badge on items */
.dash-child { font-size: 13px; }

/* Due date text */
.dash-due { font-size: 15px; }

/* Source badge (course name) */
.dash-source { font-size: 13px; }

/* Empty state text */
.dash-empty { font-size: 16px; }

/* Email action summary */
.ea-summary { font-size: 20px; }

/* Email action meta */
.ea-meta { font-size: 15px; }

/* "Open in Gmail" link */
.ea-link { font-size: 15px; }

/* Actions section header */
.actions-section-hdr { font-size: 14px; }

/* Subtab buttons */
.subtab { font-size: 16px; }

/* Add button */
.add-btn { font-size: 15px; }

/* Form section header */
.form-header { font-size: 14px; }

/* Form field labels */
.form-field label { font-size: 13px; }

/* Form inputs and selects */
.form-field input, .form-field select { font-size: 16px; }

/* Form save/cancel buttons */
.form-save { font-size: 16px; }
.form-cancel { font-size: 16px; }

/* Item card title */
.item-title { font-size: 18px; }

/* Item meta and notes */
.item-meta { font-size: 15px; }
.item-notes { font-size: 15px; }

/* Badges (overdue, today, etc.) */
.badge { font-size: 13px; }

/* Done/edit buttons */
.btn-done { font-size: 14px; }
.btn-edit { font-size: 14px; }

/* Show completed link */
.show-completed { font-size: 14px; }

/* Items empty state */
.items-empty { font-size: 16px; }

/* Code in chat messages */
.msg.bot code { font-size: 15px; }
```

- [ ] **Step 2: Deploy to server**

```bash
scp web/templates/index.html root@192.168.1.14:/opt/school/web/templates/index.html
```

- [ ] **Step 3: Hard-refresh browser** (`Ctrl+Shift+R` or Cmd+Shift+R on Mac, or hold Shift and tap reload on phone)

- [ ] **Step 4: Commit**

```bash
git add web/templates/index.html
git commit -m "fix: set explicit phone-readable font sizes in CSS"
```

---

## Task 7: Deploy and smoke-test

**Files:** None (deployment only)

- [ ] **Step 1: Build and deploy Docker image**

```bash
ssh root@192.168.1.14 "cd /opt/school && docker compose build && docker compose up -d"
```

- [ ] **Step 2: Verify cron entries are loaded**

```bash
ssh root@192.168.1.14 "docker compose exec school crontab -l 2>/dev/null || cat /etc/cron.d/school-dashboard"
```

Expected: all 5 cron entries visible (2 sync + 3 digest).

- [ ] **Step 3: Test morning digest manually**

```bash
ssh root@192.168.1.14 "docker compose exec school bash -c 'set -a && source /app/config/env && set +a && school-state digest --mode morning'"
```

Expected: output ending with `Digest sent [morning]: ...` and a push notification arrives on phone.

- [ ] **Step 4: Test afternoon and night digests**

```bash
ssh root@192.168.1.14 "docker compose exec school bash -c 'set -a && source /app/config/env && set +a && school-state digest --mode afternoon'"
ssh root@192.168.1.14 "docker compose exec school bash -c 'set -a && source /app/config/env && set +a && school-state digest --mode night'"
```

Expected: two more push notifications arrive on phone.

- [ ] **Step 5: Commit the push of font fix and digest changes together**

```bash
git push
```
