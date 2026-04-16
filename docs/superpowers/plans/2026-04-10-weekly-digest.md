# Weekly Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `build_weekly_digest()` to `digest.py` and two cron entries so Friday 3pm and Sunday 7pm ntfy pushes are sent automatically.

**Architecture:** Extend `school_dashboard/digest.py` with one new function `build_weekly_digest(mode, ...)` that reuses all existing helpers (`_load_state`, `_load_facts`, `_query_db_events`, `_call_litellm`). Two new cron entries in `docker/crontab` trigger it. Three new tests added to `tests/test_digest.py`.

**Tech Stack:** Python 3.12, pytest, unittest.mock, existing LiteLLM + ntfy delivery already in `digest.py`

---

### Task 1: Add `build_weekly_digest()` to `digest.py`

**Files:**
- Modify: `school_dashboard/digest.py` (append after `build_night_digest`, before `send_ntfy`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_digest.py`:

```python
@patch("school_dashboard.digest.requests.post")
def test_weekly_digest_friday_builds_text(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Week in review!"}}]},
    )
    from school_dashboard.digest import build_weekly_digest
    result = build_weekly_digest(
        mode="friday",
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
    )
    assert result == "Week in review!"
    mock_post.assert_called_once()


@patch("school_dashboard.digest.requests.post")
def test_weekly_digest_sunday_builds_text(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Week ahead!"}}]},
    )
    from school_dashboard.digest import build_weekly_digest
    result = build_weekly_digest(
        mode="sunday",
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
    )
    assert result == "Week ahead!"
    mock_post.assert_called_once()


def test_weekly_digest_empty_state(tmp_path, tmp_facts, tmp_db):
    """Missing state file returns graceful error string, not an exception."""
    from school_dashboard.digest import build_weekly_digest
    missing = str(tmp_path / "nonexistent.json")
    with patch("school_dashboard.digest.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "OK"}}]},
        )
        result = build_weekly_digest(
            mode="friday",
            state_path=missing,
            db_path=tmp_db,
            facts_path=tmp_facts,
            litellm_url="http://localhost:4000",
            api_key="test-key",
            model="claude-sonnet",
        )
    assert isinstance(result, str)
    assert len(result) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
pytest tests/test_digest.py::test_weekly_digest_friday_builds_text tests/test_digest.py::test_weekly_digest_sunday_builds_text tests/test_digest.py::test_weekly_digest_empty_state -v
```

Expected: FAIL with `ImportError: cannot import name 'build_weekly_digest'`

- [ ] **Step 3: Add `build_weekly_digest()` to `digest.py`**

Insert the following block in `school_dashboard/digest.py` after `build_night_digest` (before `# ── Delivery`). Also add `Literal` to the imports at the top:

Change the imports line from:
```python
from datetime import date, timedelta
```
to:
```python
from datetime import date, timedelta
from typing import Literal
```

Then add the function:

```python
def build_weekly_digest(
    mode: Literal["friday", "sunday"],
    state_path: str,
    db_path: str,
    facts_path: str,
    litellm_url: str,
    api_key: str,
    model: str,
    days_ahead: int = 7,
) -> str:
    """Build a weekly digest: friday=week in review, sunday=week ahead preview."""
    today = date.today()
    state = _load_state(state_path)
    facts = _load_facts(facts_path)

    # Collect assignments across the next `days_ahead` days
    upcoming_assignments: list[dict] = []
    for offset in range(days_ahead):
        target = (today + timedelta(days=offset)).isoformat()
        upcoming_assignments.extend(_assignments_due_on(state, target))

    # Collect DB events for the window
    upcoming_events: list[dict] = []
    for offset in range(3 if mode == "friday" else days_ahead):
        target = (today + timedelta(days=offset)).isoformat()
        upcoming_events.extend(_query_db_events(db_path, target))

    ixl = _ixl_remaining(state)

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

    if mode == "friday":
        prompt = f"""You are writing a Friday afternoon school summary for a parent. Summarize the week: what's still outstanding per child, IXL progress, anything that needs attention over the weekend. Be concise — 3-5 bullet points per child max.

Today (Friday): {today.isoformat()}

Outstanding assignments (due within next {days_ahead} days):
{assign_str}

School events in the next 3 days:
{events_str}

IXL remaining skills per child:
{ixl_str}

Known facts:
{facts_str}"""
    else:
        prompt = f"""You are writing a Sunday evening school preview for a parent. Summarize what's coming up this week: assignments due with dates, school events, and IXL targets to hit. Be practical and forward-looking — help the parent plan.

Today (Sunday): {today.isoformat()}

Assignments due in the next {days_ahead} days:
{assign_str}

School calendar events next {days_ahead} days:
{events_str}

IXL remaining skills per child:
{ixl_str}

Known facts:
{facts_str}"""

    return _call_litellm(prompt, litellm_url, api_key, model)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_digest.py::test_weekly_digest_friday_builds_text tests/test_digest.py::test_weekly_digest_sunday_builds_text tests/test_digest.py::test_weekly_digest_empty_state -v
```

Expected: 3 PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
pytest tests/test_digest.py -v
```

Expected: all tests pass (existing 9 + new 3 = 12 total)

- [ ] **Step 6: Commit**

```bash
git add school_dashboard/digest.py tests/test_digest.py
git commit -m "feat: add build_weekly_digest() for Friday/Sunday ntfy digests"
```

---

### Task 2: Add cron entries to `docker/crontab`

**Files:**
- Modify: `docker/crontab`

- [ ] **Step 1: Append two cron entries**

Append to `docker/crontab` after the existing `# Night prep` line:

```cron

# Weekly digests (no scraping — reads current state files)
# Friday 3pm: week in review
0 15 * * 5 root set -a && source /app/config/env && set +a && python3 -c "
from school_dashboard.digest import build_weekly_digest, send_ntfy
import os
text = build_weekly_digest('friday', os.environ['SCHOOL_STATE_PATH'], os.environ['SCHOOL_DB_PATH'], os.environ['SCHOOL_FACTS_PATH'], os.environ['LITELLM_URL'], os.environ['LITELLM_API_KEY'], os.environ['LITELLM_MODEL'])
send_ntfy(os.environ['NTFY_TOPIC'], text, 'Week in Review')
" >> /tmp/school-weekly.log 2>&1
# Sunday 7pm: week ahead preview
0 19 * * 0 root set -a && source /app/config/env && set +a && python3 -c "
from school_dashboard.digest import build_weekly_digest, send_ntfy
import os
text = build_weekly_digest('sunday', os.environ['SCHOOL_STATE_PATH'], os.environ['SCHOOL_DB_PATH'], os.environ['SCHOOL_FACTS_PATH'], os.environ['LITELLM_URL'], os.environ['LITELLM_API_KEY'], os.environ['LITELLM_MODEL'])
send_ntfy(os.environ['NTFY_TOPIC'], text, 'Week Ahead')
" >> /tmp/school-weekly.log 2>&1
```

- [ ] **Step 2: Verify crontab syntax visually**

Read the full file and confirm: 2 new entries added, no duplicate blank lines at top, `root` prefix present on both lines (required by this crontab format — matches existing entries).

- [ ] **Step 3: Commit**

```bash
git add docker/crontab
git commit -m "feat: add Friday/Sunday weekly digest cron entries"
```

---

### Self-Review

**Spec coverage:**
- `build_weekly_digest(mode, state_path, db_path, facts_path, litellm_url, api_key, model, days_ahead=7)` — ✅ Task 1
- Friday prompt: pending assignments, IXL remaining, events next 3 days, facts — ✅ implemented
- Sunday prompt: assignments due 7 days, calendar events 7 days, IXL targets, facts — ✅ implemented
- `send_ntfy(topic, title="Week in Review", body=text)` — ✅ cron calls `send_ntfy` with correct title args (note: `send_ntfy` signature is `send_ntfy(topic, message, title)` — matches)
- Friday cron `0 15 * * 5` — ✅ Task 2
- Sunday cron `0 19 * * 0` — ✅ Task 2
- 3 tests in `tests/test_digest.py` — ✅ Task 1

**Type consistency:** `build_weekly_digest` imports `Literal` from `typing`; `mode` typed as `Literal["friday", "sunday"]` — consistent with spec signature.

**Note on `send_ntfy` argument order:** The existing function signature is `send_ntfy(topic, message, title="School")`. The cron calls use `send_ntfy(os.environ['NTFY_TOPIC'], text, 'Week in Review')` — positional args match correctly.
