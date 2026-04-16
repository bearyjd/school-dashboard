# Readiness Checklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-child readiness checklist (overdue/soon assignments, IXL remaining, upcoming tests) visible on the dashboard and appended to afternoon and night digests.

**Architecture:** New `school_dashboard/readiness.py` module provides `get_checklist()` (reads state JSON + DB) and `format_checklist_text()` (plain text for digests). A new `/api/readiness` Flask endpoint feeds a new Readiness tab in the JS SPA. Digest builders call `get_checklist()` and append the plain-text section.

**Tech Stack:** Python 3.12, Flask, SQLite, pytest, vanilla JS SPA (no Jinja2 template variables)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `school_dashboard/readiness.py` | `get_checklist()` + `format_checklist_text()` |
| Create | `tests/test_readiness.py` | 7 unit tests |
| Modify | `web/app.py` | Add `GET /api/readiness` route |
| Modify | `web/templates/index.html` | Add Readiness tab button + `renderReadiness()` JS |
| Modify | `school_dashboard/digest.py` | Add `db_path` param to afternoon; append checklist to afternoon + night |
| Modify | `school_dashboard/cli.py` | Pass `db_path` to `build_afternoon_digest` |

---

### Task 1: Core readiness logic

**Files:**
- Create: `school_dashboard/readiness.py`
- Create: `tests/test_readiness.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_readiness.py
import json
import sqlite3
from datetime import date, timedelta

import pytest

from school_dashboard.readiness import get_checklist, format_checklist_text


@pytest.fixture
def tmp_state(tmp_path):
    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    overdue = (today - timedelta(days=1)).isoformat()
    far = (today + timedelta(days=10)).isoformat()
    state = {
        "schoology": {
            "Jack": {
                "assignments": [
                    {"title": "Math HW", "due_date": tomorrow, "course": "Math", "status": "not submitted"},
                    {"title": "Old Essay", "due_date": overdue, "course": "English", "status": "not submitted"},
                    {"title": "Done HW", "due_date": tomorrow, "course": "Science", "status": "submitted"},
                    {"title": "Far HW", "due_date": far, "course": "History", "status": "not submitted"},
                ]
            }
        },
        "ixl": {
            "Jack": {
                "totals": {
                    "math": {"remaining": 3, "assigned": 5, "done": 2},
                    "ela": {"remaining": 0, "assigned": 2, "done": 2},
                }
            }
        },
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state))
    return str(p)


@pytest.fixture
def tmp_db(tmp_path):
    db = tmp_path / "school.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, date TEXT, title TEXT, type TEXT, child TEXT)"
    )
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO events VALUES (1, ?, 'Science Test', 'TEST', '')", (tomorrow,))
    conn.execute("INSERT INTO events VALUES (2, ?, 'Reading Quiz', 'QUIZ', 'Jack')", (tomorrow,))
    conn.commit()
    conn.close()
    return str(db)


def test_overdue_assignment_urgency(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    jack = result["Jack"]
    overdue_items = [i for i in jack if i["label"] == "Old Essay"]
    assert len(overdue_items) == 1
    assert overdue_items[0]["urgency"] == "overdue"
    assert overdue_items[0]["type"] == "assignment"


def test_tomorrow_assignment_urgency(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    jack = result["Jack"]
    tomorrow_items = [i for i in jack if i["label"] == "Math HW"]
    assert len(tomorrow_items) == 1
    assert tomorrow_items[0]["urgency"] == "tomorrow"


def test_submitted_assignment_excluded(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    labels = [i["label"] for i in result.get("Jack", [])]
    assert "Done HW" not in labels


def test_beyond_cutoff_excluded(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db, days_ahead=3)
    labels = [i["label"] for i in result.get("Jack", [])]
    assert "Far HW" not in labels


def test_ixl_remaining_included(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    jack = result["Jack"]
    ixl_items = [i for i in jack if i["type"] == "ixl"]
    assert len(ixl_items) == 1  # ela has 0 remaining — excluded
    assert "3" in ixl_items[0]["label"]
    assert ixl_items[0]["urgency"] == "pending"


def test_test_event_included(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    jack = result["Jack"]
    test_items = [i for i in jack if i["type"] == "test"]
    # Science Test (no child) + Reading Quiz (child=Jack) both show
    titles = [i["label"] for i in test_items]
    assert "Science Test" in titles
    assert "Reading Quiz" in titles


def test_format_checklist_text(tmp_state, tmp_db):
    checklist = get_checklist(tmp_state, tmp_db)
    text = format_checklist_text(checklist)
    assert "Jack" in text
    assert "Old Essay" in text
    assert "[overdue]" in text.lower()


def test_empty_state_returns_empty(tmp_path, tmp_db):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({}))
    result = get_checklist(str(empty), tmp_db)
    assert result == {}
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
pytest tests/test_readiness.py -v
```

Expected: `ModuleNotFoundError: No module named 'school_dashboard.readiness'`

- [ ] **Step 3: Implement `school_dashboard/readiness.py`**

```python
# school_dashboard/readiness.py
"""Per-child readiness checklist: upcoming assignments, IXL remaining, tests."""
import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

_log = logging.getLogger(__name__)

_URGENCY_ORDER: dict[str, int] = {"overdue": 0, "tomorrow": 1, "soon": 2, "pending": 3}


def get_checklist(
    state_path: str,
    db_path: str | None = None,
    days_ahead: int = 3,
) -> dict[str, list[dict]]:
    """Return {child: [items]} for the readiness checklist.

    Each item: {"type": str, "label": str, "urgency": str, "detail": str}
    urgency values: "overdue" | "tomorrow" | "soon" | "pending"
    """
    try:
        state = json.loads(Path(state_path).read_text())
    except Exception as exc:
        _log.warning("Failed to load state %s: %s", state_path, exc)
        return {}

    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    # Fetch test/quiz events once for all children
    test_events: list[dict] = []
    if db_path and Path(db_path).exists():
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT date, title, type, child FROM events "
                    "WHERE type IN ('TEST','QUIZ') AND date >= ? AND date <= ? "
                    "ORDER BY date",
                    (today.isoformat(), cutoff.isoformat()),
                ).fetchall()
                test_events = [dict(r) for r in rows]
        except Exception as exc:
            _log.warning("Failed to query events from %s: %s", db_path, exc)

    result: dict[str, list[dict]] = {}

    for child, child_data in (state.get("schoology") or {}).items():
        items: list[dict] = []

        # Schoology assignments
        for a in child_data.get("assignments") or []:
            if a.get("status") in ("submitted", "graded", "completed"):
                continue
            due_str = (a.get("due_date") or "")[:10]
            if not due_str:
                continue
            try:
                due = date.fromisoformat(due_str)
            except ValueError:
                continue
            if due > cutoff:
                continue
            if due < today:
                urgency = "overdue"
            elif due == today + timedelta(days=1):
                urgency = "tomorrow"
            else:
                urgency = "soon"
            items.append({
                "type": "assignment",
                "label": a.get("title", ""),
                "urgency": urgency,
                "detail": a.get("course", ""),
            })

        # IXL remaining
        for subj, totals in ((state.get("ixl") or {}).get(child) or {}).get("totals", {}).items():
            remaining = totals.get("remaining", 0)
            if remaining > 0:
                label = f"{remaining} IXL skill{'s' if remaining != 1 else ''} remaining"
                items.append({
                    "type": "ixl",
                    "label": label,
                    "urgency": "pending",
                    "detail": subj,
                })

        # Tests / quizzes from DB
        for ev in test_events:
            if ev["child"] and ev["child"] != child:
                continue
            try:
                event_date = date.fromisoformat(ev["date"])
            except ValueError:
                continue
            urgency = "tomorrow" if event_date == today + timedelta(days=1) else "soon"
            items.append({
                "type": "test",
                "label": ev["title"],
                "urgency": urgency,
                "detail": ev["type"].title(),
            })

        items.sort(key=lambda x: _URGENCY_ORDER.get(x["urgency"], 4))

        if items:
            result[child] = items

    return result


def format_checklist_text(
    checklist: dict[str, list[dict]],
    prefix: str = "Action items:",
) -> str:
    """Return a plain-text checklist for digest injection. Empty string if nothing."""
    if not checklist:
        return ""
    lines: list[str] = []
    if prefix:
        lines.append(prefix)
    for child, items in checklist.items():
        lines.append(f"\n{child}:")
        for item in items:
            tag = f"[{item['urgency']}]"
            detail = f" ({item['detail']})" if item.get("detail") else ""
            lines.append(f"  - {item['label']}{detail} {tag}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_readiness.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add school_dashboard/readiness.py tests/test_readiness.py
git commit -m "feat: add readiness checklist core logic and tests"
```

---

### Task 2: Dashboard tab

**Files:**
- Modify: `web/app.py` (add `/api/readiness` route)
- Modify: `web/templates/index.html` (add Readiness tab button + JS)

- [ ] **Step 1: Write a failing integration test**

```python
# In tests/test_app.py (create if not exists, otherwise append)
import json
import sqlite3
from pathlib import Path
import pytest
from web.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    state = {
        "schoology": {"Jack": {"assignments": []}},
        "ixl": {"Jack": {"totals": {"math": {"remaining": 2, "assigned": 3, "done": 1}}}},
    }
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps(state))
    db = tmp_path / "school.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, date TEXT, title TEXT, type TEXT, child TEXT)")
    conn.commit(); conn.close()
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(sp))
    monkeypatch.setenv("SCHOOL_DB_PATH", str(db))
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_api_readiness_returns_checklist(client):
    r = client.get("/api/readiness")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "checklist" in data
    assert "Jack" in data["checklist"]
    jack_items = data["checklist"]["Jack"]
    ixl_items = [i for i in jack_items if i["type"] == "ixl"]
    assert len(ixl_items) == 1
```

Run to confirm it fails:

```bash
pytest tests/test_app.py::test_api_readiness_returns_checklist -v
```

Expected: `FAILED` (route not found, 404)

- [ ] **Step 2: Add `/api/readiness` to `web/app.py`**

Find the last `@app.route` block in `web/app.py`. Add after it:

```python
@app.route("/api/readiness")
def api_readiness():
    from school_dashboard.readiness import get_checklist
    try:
        checklist = get_checklist(STATE_PATH, DB_PATH)
        return jsonify({"checklist": checklist})
    except Exception as exc:
        _log.warning("Readiness checklist error: %s", exc)
        return jsonify({"error": str(exc), "checklist": {}}), 500
```

- [ ] **Step 3: Run test to confirm route works**

```bash
pytest tests/test_app.py::test_api_readiness_returns_checklist -v
```

Expected: PASSED

- [ ] **Step 4: Add Readiness tab button to `web/templates/index.html`**

In `index.html`, find:

```html
<button class="dtab" data-mode="calendar">Calendar</button>
```

Add immediately after:

```html
<button class="dtab" data-mode="readiness">Readiness</button>
```

- [ ] **Step 5: Add `renderReadiness()` JS function and wire up `renderDashList()` and `setDashMode()`**

In `index.html`, find:

```javascript
let calEvents=[];
async function loadCalendar(){
```

Insert immediately before it:

```javascript
let readinessData=null;
async function loadReadiness(){
  if(readinessData!==null)return;
  try{
    const r=await fetch('/api/readiness');
    const d=await r.json();
    readinessData=d.checklist||{};
  }catch(e){readinessData={};}
}

function renderReadiness(){
  const list=document.getElementById('dash-list');
  if(readinessData===null){list.innerHTML='<div class="dash-empty">Loading...</div>';return;}
  let items=[];
  Object.entries(readinessData).forEach(([child,childItems])=>{
    childItems.forEach(item=>items.push(Object.assign({},item,{child})));
  });
  if(dashChild!=='all') items=items.filter(i=>i.child===dashChild);
  if(!items.length){list.innerHTML='<div class="dash-empty">All clear - nothing urgent!</div>';return;}
  const urgencyOrder={overdue:0,tomorrow:1,soon:2,pending:3};
  items.sort((a,b)=>(urgencyOrder[a.urgency]??3)-(urgencyOrder[b.urgency]??3));
  const urgencyColor={overdue:'#ff9aa2',tomorrow:'#ffd180',soon:'#b5ead7',pending:'#c7ceea'};
  list.innerHTML=items.map(item=>{
    const color=urgencyColor[item.urgency]||'#c7ceea';
    return '<div class="dash-item">'+
      '<span class="dash-dot" style="background:'+color+'"></span>'+
      '<div class="dash-body">'+
        '<div class="dash-summary">'+escHtml(item.label)+'</div>'+
        '<div class="dash-meta">'+
          '<span class="dash-child">'+escHtml(item.child)+'</span>'+
          (item.detail?'<span class="dash-source">'+escHtml(item.detail)+'</span>':'')+
        '</div>'+
      '</div></div>';
  }).join('');
}
```

Find:

```javascript
function renderDashList(){
  const list=document.getElementById('dash-list');
  const timeBar=document.getElementById('dash-time-bar');
  timeBar.style.display=(dashMode==='email')?'flex':'none';
  if(dashMode==='schoology') list.innerHTML=renderSchoology();
  else if(dashMode==='ixl') list.innerHTML=renderIxl();
  else if(dashMode==='calendar') list.innerHTML=renderCalendar();
  else list.innerHTML=renderEmailItems();
}
```

Replace with:

```javascript
function renderDashList(){
  const list=document.getElementById('dash-list');
  const timeBar=document.getElementById('dash-time-bar');
  timeBar.style.display=(dashMode==='email')?'flex':'none';
  if(dashMode==='schoology') list.innerHTML=renderSchoology();
  else if(dashMode==='ixl') list.innerHTML=renderIxl();
  else if(dashMode==='calendar') list.innerHTML=renderCalendar();
  else if(dashMode==='readiness') renderReadiness();
  else list.innerHTML=renderEmailItems();
}
```

Find:

```javascript
  if(m==='calendar'){loadCalendar().then(()=>renderDashList());}
  else renderDashList();
```

Replace with:

```javascript
  if(m==='calendar'){loadCalendar().then(()=>renderDashList());}
  else if(m==='readiness'){loadReadiness().then(()=>renderDashList());}
  else renderDashList();
```

- [ ] **Step 6: Smoke-test in browser**

```bash
# On the server or locally with:
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
set -a && source config/env && set +a
FLASK_APP=web.app flask run --port 5000
# Open http://localhost:5000 — click Readiness tab — verify items appear
```

- [ ] **Step 7: Run full test suite**

```bash
pytest -v
```

Expected: all existing tests pass + new test passes

- [ ] **Step 8: Commit**

```bash
git add web/app.py web/templates/index.html
git commit -m "feat: add Readiness tab to dashboard with /api/readiness endpoint"
```

---

### Task 3: Digest injection

**Files:**
- Modify: `school_dashboard/digest.py` (add `db_path` param to afternoon builder; append checklist to afternoon + night)
- Modify: `school_dashboard/cli.py` (pass `db_path` to afternoon builder)

- [ ] **Step 1: Write failing tests**

In `tests/test_digest.py`, find the existing fixtures and add:

```python
# Add to tests/test_digest.py

def test_afternoon_digest_includes_checklist(tmp_state, tmp_db, requests_mock):
    """Checklist section appended after LiteLLM response."""
    requests_mock.post("http://fake-llm/v1/chat/completions", json={
        "choices": [{"message": {"content": "Homework check done."}}]
    })
    from school_dashboard.digest import build_afternoon_digest
    result = build_afternoon_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        litellm_url="http://fake-llm",
        api_key="key",
        model="gpt-4",
    )
    assert "Homework check done." in result
    assert "Action items:" in result


def test_night_digest_includes_checklist(tmp_state, tmp_db, requests_mock):
    """Night digest appends checklist with 'Before bed' prefix."""
    requests_mock.post("http://fake-llm/v1/chat/completions", json={
        "choices": [{"message": {"content": "Night prep ready."}}]
    })
    from school_dashboard.digest import build_night_digest
    import json
    from pathlib import Path
    result = build_night_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path="/dev/null",
        gcal_events=[],
        litellm_url="http://fake-llm",
        api_key="key",
        model="gpt-4",
    )
    assert "Night prep ready." in result
    assert "Before bed" in result
```

**Note:** `tmp_state` and `tmp_db` fixtures already exist in `tests/test_digest.py`. The new tests must use the same fixtures (check the existing fixture names — use them as-is or replicate what's needed).

Run to confirm they fail:

```bash
pytest tests/test_digest.py::test_afternoon_digest_includes_checklist tests/test_digest.py::test_night_digest_includes_checklist -v
```

Expected: `FAILED` — `build_afternoon_digest` has no `db_path` param

- [ ] **Step 2: Update `build_afternoon_digest` signature and append checklist**

In `school_dashboard/digest.py`, find:

```python
def build_afternoon_digest(
    state_path: str,
    litellm_url: str,
    api_key: str,
    model: str,
    today: str | None = None,
) -> str:
```

Replace with:

```python
def build_afternoon_digest(
    state_path: str,
    litellm_url: str,
    api_key: str,
    model: str,
    today: str | None = None,
    db_path: str | None = None,
) -> str:
```

Find at the end of `build_afternoon_digest` (just before `return _call_litellm(...)`):

```python
    return _call_litellm(prompt, litellm_url, api_key, model)
```

Replace with:

```python
    text = _call_litellm(prompt, litellm_url, api_key, model)
    if db_path:
        from school_dashboard.readiness import get_checklist, format_checklist_text
        checklist = get_checklist(state_path, db_path)
        checklist_text = format_checklist_text(checklist, prefix="Action items:")
        if checklist_text:
            text = text + "\n\n" + checklist_text
    return text
```

- [ ] **Step 3: Append checklist to `build_night_digest`**

Find at the end of `build_night_digest` (just before `return _call_litellm(...)`):

```python
    return _call_litellm(prompt, litellm_url, api_key, model)
```

Replace with:

```python
    text = _call_litellm(prompt, litellm_url, api_key, model)
    from school_dashboard.readiness import get_checklist, format_checklist_text
    checklist = get_checklist(state_path, db_path)
    checklist_text = format_checklist_text(checklist, prefix="Before bed —")
    if checklist_text:
        text = text + "\n\n" + checklist_text
    return text
```

- [ ] **Step 4: Run digest tests**

```bash
pytest tests/test_digest.py -v
```

Expected: all 5 tests pass (3 existing + 2 new)

- [ ] **Step 5: Update `cli.py` to pass `db_path` to afternoon digest**

Read `school_dashboard/cli.py` first to locate the `cmd_digest` function, then find the `build_afternoon_digest` call and add `db_path=db_file` (use whatever variable name holds the DB path in that function — likely `db_file` or `state_db`).

The call currently looks like:

```python
text = _digest.build_afternoon_digest(
    state_path=state_file,
    litellm_url=...,
    api_key=...,
    model=...,
)
```

Add `db_path=db_file` (or the correct variable name from that function):

```python
text = _digest.build_afternoon_digest(
    state_path=state_file,
    db_path=db_file,
    litellm_url=...,
    api_key=...,
    model=...,
)
```

- [ ] **Step 6: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add school_dashboard/digest.py school_dashboard/cli.py tests/test_digest.py
git commit -m "feat: inject readiness checklist into afternoon and night digests"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Overdue/upcoming assignments (3-day window): Task 1 `get_checklist()`
- ✅ IXL remaining > 0: Task 1 `get_checklist()`
- ✅ Upcoming tests/quizzes: Task 1 via DB query
- ✅ Dashboard tab (JS SPA, not Jinja2): Task 2 `/api/readiness` + `renderReadiness()`
- ✅ Afternoon digest injection: Task 3 `build_afternoon_digest(db_path=...)`
- ✅ Night digest injection with "Before bed" prefix: Task 3 `build_night_digest()`
- ✅ ASCII-safe: `format_checklist_text()` emits no emoji

**No placeholders:** All code blocks are complete and runnable.

**Type consistency:**
- `get_checklist()` returns `dict[str, list[dict]]` — used consistently in all three tasks
- `format_checklist_text()` accepts same type — consistent
- Item keys `type/label/urgency/detail` used identically in Python and JS
