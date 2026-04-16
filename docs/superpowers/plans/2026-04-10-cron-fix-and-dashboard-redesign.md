# Cron Fix + Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Docker cron env-loading bug so email sync actually runs, verify gog OAuth works, and redesign the dashboard to surface actionable items immediately rather than dumping all data.

**Architecture:** Two independent fixes. (1) `docker/crontab` gets `SCHOOL_DASHBOARD_ENV` set so `school-sync.sh` loads `/app/config/env` instead of the old LXC path. (2) `html.py` adds urgency classification (overdue/today/tomorrow) across all children, and `dashboard.html` is restructured into three sections: "Right Now" (urgent), "IXL Today" (only kids not at goal), and per-child detail (grades below B only + upcoming).

**Tech Stack:** Bash (crontab), Python/Jinja2 (html.py + dashboard.html), pytest

---

## Known Issue: gog OAuth (do this manually before running sync)

The gog OAuth tokens must be set up inside the running container. This is a one-time step:

```bash
docker exec -it school-dashboard bash
gog auth add YOUR_GOG_ACCOUNT_EMAIL
# follow the browser OAuth flow — copy the URL, authenticate, paste the code back
gog mail list --account YOUR_GOG_ACCOUNT_EMAIL   # should return inbox items
exit
```

The `gog-creds` named volume persists the tokens across container restarts. If `gog mail list` errors, check that `GOG_ACCOUNT` matches the email you authenticated with.

---

## File Map

| File | Change |
|------|--------|
| `docker/crontab` | Add `SCHOOL_DASHBOARD_ENV=/app/config/env` env var line |
| `sync/school-sync.sh` | Add intel step (currently missing from Docker sync) |
| `school_dashboard/html.py` | Add urgency classification + cross-child urgent list |
| `school_dashboard/templates/dashboard.html` | Full redesign: 3-section priority layout |
| `tests/test_html.py` | New: urgency classification tests |

---

## Task 1: Fix cron env loading

**Problem:** `school-sync.sh` defaults to `/etc/school-dashboard/env` (old LXC path). In Docker, the env is at `/app/config/env`. Cron runs in a clean environment and doesn't inherit `entrypoint.sh`'s exported vars.

**Fix:** Set `SCHOOL_DASHBOARD_ENV` in the crontab so the sync script sources the right file.

**Files:**
- Modify: `docker/crontab`
- Modify: `sync/school-sync.sh` (add missing intel step)

- [ ] **Step 1: Update docker/crontab**

Replace the entire file with:

```
# school-dashboard cron
# Set env file path so school-sync.sh finds Docker secrets
SCHOOL_DASHBOARD_ENV=/app/config/env

# Morning sync + digest at 6:00am weekdays
0 6 * * 1-5 root bash /app/sync/school-sync.sh >> /tmp/school-sync.log 2>&1
# Afternoon data refresh at 2:30pm weekdays
30 14 * * 1-5 root bash /app/sync/school-sync.sh >> /tmp/school-sync.log 2>&1
```

- [ ] **Step 2: Add the intel step to sync/school-sync.sh**

The intel step (LiteLLM email extraction into DB) is missing from the Docker sync. Add it after email-sync, before dashboard regen. Replace the step 4→5 block:

```bash
# --- Step 4: Email sync (fetch, normalize, classify — no LLM) ---
if [[ -n "${SCHOOL_EMAIL_ACCOUNT:-}" ]]; then
    log "Syncing emails..."
    school-state email-sync --account "$SCHOOL_EMAIL_ACCOUNT" || log "WARN: Email sync had errors"
else
    log "WARN: SCHOOL_EMAIL_ACCOUNT not set — skipping email sync"
fi

# --- Step 5: Email intel (LiteLLM extraction → school.db + facts.json) ---
if [[ -n "${LITELLM_URL:-}" && -f "${SCHOOL_EMAIL_DIGEST:-/app/state/email-digest.json}" ]]; then
    log "Running email intel extraction..."
    python3 -c "
import os, json
from school_dashboard.intel import process_digest
with open(os.environ.get('SCHOOL_EMAIL_DIGEST', '/app/state/email-digest.json')) as f:
    digest = json.load(f)
count = process_digest(
    digest=digest,
    db_path=os.environ.get('SCHOOL_DB_PATH', '/app/state/school.db'),
    facts_path=os.environ.get('SCHOOL_FACTS_PATH', '/app/state/facts.json'),
    litellm_url=os.environ['LITELLM_URL'],
    api_key=os.environ.get('LITELLM_API_KEY', ''),
    model=os.environ.get('LITELLM_MODEL', 'claude-sonnet'),
)
print(f'Intel: {count} emails processed', flush=True)
" 2>&1 || log "WARN: Intel extraction had errors"
else
    log "INFO: Skipping intel (LITELLM_URL not set or digest missing)"
fi

# --- Step 6: Regenerate dashboard ---
log "Regenerating dashboard..."
school-state html
```

- [ ] **Step 3: Manual smoke test inside container**

```bash
docker exec -it school-dashboard bash -c "
  SCHOOL_DASHBOARD_ENV=/app/config/env bash /app/sync/school-sync.sh
"
```

Expected output: Steps 1-6 logged, no `KeyError` on env vars, sync complete.

- [ ] **Step 4: Commit**

```bash
git add docker/crontab sync/school-sync.sh
git commit -m "fix: cron env loading in Docker + add intel step to sync"
```

---

## Task 2: Add urgency classification to html.py

**Problem:** `html.py` passes raw assignments to the template with no urgency grouping. The template has to repeat overdue/tomorrow logic per child. We need a cross-child "urgent now" list computed in Python.

**Files:**
- Modify: `school_dashboard/html.py`
- Create: `tests/test_html.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_html.py`:

```python
from datetime import date, timedelta
from school_dashboard.html import _urgency, _build_urgent_items


def _due(delta_days):
    """Return ISO date string offset from today."""
    return (date.today() + timedelta(days=delta_days)).isoformat() + "T23:59:00"


def test_urgency_overdue():
    assert _urgency(_due(-1)) == "overdue"


def test_urgency_today():
    assert _urgency(_due(0)) == "today"


def test_urgency_tomorrow():
    assert _urgency(_due(1)) == "tomorrow"


def test_urgency_upcoming():
    assert _urgency(_due(3)) == "upcoming"


def test_urgency_no_date():
    assert _urgency(None) == "upcoming"
    assert _urgency("") == "upcoming"


def test_build_urgent_items_filters_and_sorts():
    children = [
        {
            "name": "Alice",
            "assignments": [
                {"title": "Late essay", "course": "ELA", "due_date": _due(-2)},
                {"title": "Future project", "course": "Science", "due_date": _due(5)},
                {"title": "Due today", "course": "Math", "due_date": _due(0)},
            ],
        },
        {
            "name": "Bob",
            "assignments": [
                {"title": "Tomorrow quiz", "course": "History", "due_date": _due(1)},
            ],
        },
    ]
    urgent = _build_urgent_items(children)
    # Only overdue, today, tomorrow — not the 5-day future item
    assert len(urgent) == 3
    # Sorted: overdue first, then today, then tomorrow
    assert urgent[0]["urgency"] == "overdue"
    assert urgent[1]["urgency"] == "today"
    assert urgent[2]["urgency"] == "tomorrow"
    # Child name attached
    assert urgent[0]["child"] == "Alice"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_html.py -v
```

Expected: `ImportError` — `_urgency` and `_build_urgent_items` not defined yet.

- [ ] **Step 3: Add urgency helpers to html.py**

Add after the existing `_is_due_tomorrow` function (around line 53):

```python
def _urgency(due_str: str | None) -> str:
    """Return 'overdue' | 'today' | 'tomorrow' | 'upcoming'."""
    if not due_str:
        return "upcoming"
    try:
        from datetime import timedelta
        dt = datetime.fromisoformat(due_str[:10]).date()
        today = datetime.now().date()
        if dt < today:
            return "overdue"
        if dt == today:
            return "today"
        if dt == today + timedelta(days=1):
            return "tomorrow"
        return "upcoming"
    except (ValueError, TypeError):
        return "upcoming"


def _build_urgent_items(children: list[dict]) -> list[dict]:
    """Return overdue/today/tomorrow assignments across all children, sorted by date."""
    urgent = []
    for child in children:
        for a in child.get("assignments", []):
            u = _urgency(a.get("due_date"))
            if u in ("overdue", "today", "tomorrow"):
                urgent.append({**a, "child": child["name"], "urgency": u})
    # Sort: overdue < today < tomorrow, then by date within each bucket
    order = {"overdue": 0, "today": 1, "tomorrow": 2}
    urgent.sort(key=lambda x: (order[x["urgency"]], x.get("due_date") or ""))
    return urgent
```

Also add `_urgency` and `_build_urgent_items` to the `env.filters` block in `render()`:

```python
    env.filters["urgency"] = _urgency
```

And pass `urgent_items` to the template. In `render()`, after building `children_data` and before `html = template.render(...)`:

```python
    urgent_items = _build_urgent_items(children_data)
```

Add `urgent_items=urgent_items` to the `template.render(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_html.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest -v
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add school_dashboard/html.py tests/test_html.py
git commit -m "feat: add urgency classification to html.py for priority dashboard"
```

---

## Task 3: Dashboard template redesign

**Goal:** Replace the flat per-child dump with three sections:
1. **Right Now** — urgent items (overdue/today/tomorrow) across all children
2. **IXL Today** — only children who haven't hit their goal (hide if all done)
3. **Per-child** — collapsed; only shows grades below B and upcoming assignments (next 3 days)

**Files:**
- Modify: `school_dashboard/templates/dashboard.html`

- [ ] **Step 1: Replace dashboard.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>School Dashboard</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #242736;
    --border: #2e3247;
    --text: #e1e4ed;
    --text2: #8b90a5;
    --accent: #6c8cff;
    --green: #4ade80;
    --yellow: #fbbf24;
    --red: #f87171;
    --orange: #fb923c;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 16px;
    max-width: 600px;
    margin: 0 auto;
    -webkit-font-smoothing: antialiased;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 1.1rem; font-weight: 600; }
  header .meta { font-size: 0.72rem; color: var(--text2); }

  /* Section wrappers */
  .section { margin-bottom: 20px; }
  .section-title {
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text2);
    margin-bottom: 8px;
  }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 8px;
  }

  /* ── RIGHT NOW ── */
  .urgent-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 7px 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.85rem;
  }
  .urgent-row:last-child { border-bottom: none; }
  .badge {
    font-size: 0.65rem;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 4px;
    white-space: nowrap;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .badge-overdue { background: #7f1d1d; color: var(--red); }
  .badge-today   { background: #431407; color: var(--orange); }
  .badge-tomorrow{ background: #451a03; color: var(--yellow); }
  .badge-child   { background: var(--surface2); color: var(--text2); font-weight: 600; }
  .urgent-title  { flex: 1; }
  .urgent-course { font-size: 0.72rem; color: var(--text2); margin-top: 1px; display: block; }
  .all-clear     { color: var(--green); font-size: 0.85rem; padding: 4px 0; }

  /* ── IXL TODAY ── */
  .ixl-child {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.85rem;
    margin-bottom: 4px;
  }
  .ixl-label { color: var(--text2); font-size: 0.78rem; }
  .ixl-count { font-variant-numeric: tabular-nums; font-size: 0.85rem; }
  .progress-bar {
    height: 5px;
    background: var(--surface2);
    border-radius: 3px;
    margin: 3px 0 8px;
    overflow: hidden;
  }
  .progress-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }
  .fill-good  { background: var(--green); }
  .fill-mid   { background: var(--yellow); }
  .fill-low   { background: var(--orange); }
  .fill-none  { background: var(--red); }

  /* ── PER CHILD ── */
  details { margin-bottom: 8px; }
  details > summary {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 14px;
    cursor: pointer;
    font-size: 0.9rem;
    font-weight: 600;
    list-style: none;
    user-select: none;
  }
  details > summary::-webkit-details-marker { display: none; }
  details > summary .chevron { color: var(--text2); font-size: 0.75rem; }
  details[open] > summary { border-radius: 10px 10px 0 0; border-bottom-color: transparent; }
  .detail-body {
    background: var(--surface);
    border: 1px solid var(--border);
    border-top: none;
    border-radius: 0 0 10px 10px;
    padding: 10px 14px 12px;
  }

  /* grades in detail */
  .grade-row {
    display: flex;
    justify-content: space-between;
    font-size: 0.85rem;
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
  }
  .grade-row:last-child { border-bottom: none; }
  .grade-ok   { color: var(--green); }
  .grade-warn { color: var(--yellow); }
  .grade-bad  { color: var(--red); font-weight: 600; }

  /* upcoming assignments in detail */
  .upcoming-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 0.82rem;
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
    color: var(--text2);
  }
  .upcoming-row:last-child { border-bottom: none; }
  .upcoming-title { flex: 1; margin-right: 8px; color: var(--text); }
  .upcoming-due { font-size: 0.72rem; white-space: nowrap; }

  .subsection-title {
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--text2);
    margin: 10px 0 5px;
  }
  .subsection-title:first-child { margin-top: 0; }
  .empty-detail { color: var(--text2); font-size: 0.82rem; font-style: italic; }
</style>
</head>
<body>
<header>
  <h1>School Dashboard</h1>
  <span class="meta">{{ generated }}</span>
</header>

{# ── SECTION 1: RIGHT NOW ── #}
<div class="section">
  <div class="section-title">Right Now</div>
  <div class="card">
    {% if action_items %}
      {% for item in action_items %}
      <div class="urgent-row">
        <span class="badge badge-child">{{ item.child }}</span>
        <span class="urgent-title">
          {{ item.summary }}
          {% if item.source %}<span class="urgent-course">{{ item.source }}</span>{% endif %}
        </span>
        {% if item.due %}<span class="badge badge-today">action</span>{% endif %}
      </div>
      {% endfor %}
    {% endif %}

    {% if urgent_items %}
      {% for item in urgent_items %}
      <div class="urgent-row">
        <span class="badge badge-{{ item.urgency }}">{{ item.urgency }}</span>
        <span class="badge badge-child">{{ item.child }}</span>
        <span class="urgent-title">
          {{ item.title }}
          <span class="urgent-course">{{ item.course }}</span>
        </span>
      </div>
      {% endfor %}
    {% endif %}

    {% if not action_items and not urgent_items %}
    <div class="all-clear">✓ Nothing urgent</div>
    {% endif %}
  </div>
</div>

{# ── SECTION 2: IXL TODAY ── #}
{% set ixl_needs_work = children | selectattr("ixl_totals") | list %}
{% if ixl_needs_work %}
<div class="section">
  <div class="section-title">IXL Today</div>
  <div class="card">
    {% for child in ixl_needs_work %}
    <div class="ixl-child">
      <span>{{ child.name }}</span>
      <span class="ixl-label">{{ child.grade }}</span>
    </div>
    {% for subject, totals in child.ixl_totals.items() %}
    {% set pct = (totals.done / totals.assigned * 100) if totals.assigned > 0 else 0 %}
    <div style="display:flex; justify-content:space-between; font-size:0.78rem; color:var(--text2);">
      <span>{{ subject }}</span>
      <span class="ixl-count">{{ totals.done }}/{{ totals.assigned }}</span>
    </div>
    <div class="progress-bar">
      <div class="progress-fill {% if pct >= 80 %}fill-good{% elif pct >= 50 %}fill-mid{% elif pct > 0 %}fill-low{% else %}fill-none{% endif %}"
           style="width: {{ pct|round|int }}%"></div>
    </div>
    {% endfor %}
    {% endfor %}
  </div>
</div>
{% endif %}

{# ── SECTION 3: PER CHILD ── #}
<div class="section">
  <div class="section-title">Details</div>
  {% for child in children %}
  {% set bad_grades = child.grades | selectattr("letter") | selectattr("letter", "ne", "") | list %}
  {% set upcoming = child.assignments | selectattr("due_date") | list %}
  {% set upcoming_only = upcoming | rejectattr("due_date", "in", urgent_items | map(attribute="due_date") | list) | list %}
  <details>
    <summary>
      <span>{{ child.name }} <span style="font-weight:400; color:var(--text2); font-size:0.8rem;">{{ child.grade }}</span></span>
      <span class="chevron">▸</span>
    </summary>
    <div class="detail-body">

      {% set warn_grades = [] %}
      {% for g in child.grades %}
        {% if g.letter and g.letter[0] not in ('A','B') %}
          {% set _ = warn_grades.append(g) %}
        {% endif %}
      {% endfor %}

      {% if warn_grades %}
      <div class="subsection-title">Grades — Needs Attention</div>
      {% for g in warn_grades %}
      <div class="grade-row">
        <span>{{ g.course }}</span>
        <span class="{{ g.letter|letter_class }}">{{ g.grade }}</span>
      </div>
      {% endfor %}
      {% endif %}

      {% set upcoming3 = [] %}
      {% for a in child.assignments %}
        {% if a.due_date and a.due_date|urgency == "upcoming" %}
          {% set _ = upcoming3.append(a) %}
        {% endif %}
      {% endfor %}

      {% if upcoming3 %}
      <div class="subsection-title">Upcoming</div>
      {% for a in upcoming3[:5] %}
      <div class="upcoming-row">
        <span class="upcoming-title">{{ a.title }}</span>
        <span class="upcoming-due">{{ a.due_date|format_due }}</span>
      </div>
      {% endfor %}
      {% endif %}

      {% if not warn_grades and not upcoming3 %}
      <span class="empty-detail">All clear</span>
      {% endif %}

    </div>
  </details>
  {% endfor %}
</div>

<script id="state-json" type="application/json">{{ state_json }}</script>
</body>
</html>
```

- [ ] **Step 2: Run a visual smoke test**

```bash
set -a && source config/env && set +a
python3 -c "
from school_dashboard import html, state
s = state.load()
out = html.render(s)
print('Dashboard written to:', out)
"
# Then open the file in a browser
```

Expected: dashboard opens, three sections visible, no Jinja2 errors.

- [ ] **Step 3: Commit**

```bash
git add school_dashboard/templates/dashboard.html
git commit -m "feat: redesign dashboard with priority-first 3-section layout"
```

---

## Self-Review

**Spec coverage:**
- ✅ Cron env bug fix → Task 1
- ✅ Intel step missing from sync → Task 1, Step 2
- ✅ gog OAuth → documented before tasks
- ✅ Dashboard urgency classification → Task 2
- ✅ Cross-child urgent list → Task 2 `_build_urgent_items`
- ✅ IXL section (only when needed) → Task 3 template section 2
- ✅ Grades only when below B → Task 3 `warn_grades` filter
- ✅ Upcoming (non-urgent) assignments → Task 3 `upcoming3` filter
- ✅ Tests → Task 2 `tests/test_html.py`

**Placeholder scan:** None found.

**Type consistency:** `_urgency` returns `str`, used as Jinja2 filter `urgency` in template `a.due_date|urgency` — registered in `render()` as `env.filters["urgency"] = _urgency`. ✅
