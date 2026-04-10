# Family Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `items` table to school.db, three Flask API routes, and a Manage tab to the web UI so both parents can add/edit/complete assignments and extracurricular events.

**Architecture:** A new `school_dashboard/db.py` module owns the SQLite `items` table and all CRUD helpers. `web/app.py` gains three routes that call those helpers. `web/templates/index.html` gains a third tab with vanilla-JS CRUD UI — sub-tabs per child, inline add form, ✓ Done and ✎ Edit on each row, completed items hidden by default.

**Tech Stack:** Python 3.12, SQLite (stdlib sqlite3), Flask, vanilla JS (no new dependencies)

---

## File Map

| File | Change |
|------|--------|
| `school_dashboard/db.py` | **Create** — items table DDL + 6 helper functions |
| `tests/test_items.py` | **Create** — 7 unit tests for db helpers |
| `web/app.py` | **Modify** — add 4 routes: GET/POST `/api/items`, PATCH/DELETE `/api/items/<id>` |
| `tests/test_api_items.py` | **Create** — 4 Flask client tests |
| `web/templates/index.html` | **Modify** — add Manage tab button, panel HTML, and JS |

---

## Task 1: items table and db helpers

**Files:**
- Create: `school_dashboard/db.py`
- Create: `tests/test_items.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_items.py`:

```python
import pytest
from school_dashboard.db import (
    init_db, create_item, update_item, complete_item,
    list_items, delete_item, item_exists_for_email,
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def test_create_and_list_items(db):
    item_id = create_item(db, child="Alice", title="Math test", due_date="2026-05-01")
    items = list_items(db)
    assert len(items) == 1
    assert items[0]["id"] == item_id
    assert items[0]["child"] == "Alice"
    assert items[0]["title"] == "Math test"
    assert items[0]["completed"] == 0
    assert items[0]["source"] == "manual"


def test_complete_item(db):
    item_id = create_item(db, child="Alice", title="Soccer game")
    result = complete_item(db, item_id)
    assert result is True
    items = list_items(db, include_completed=True)
    assert items[0]["completed"] == 1
    assert items[0]["completed_at"] is not None


def test_update_item(db):
    item_id = create_item(db, child="Alice", title="Old title", notes=None)
    update_item(db, item_id, title="New title", notes="Bring cleats")
    items = list_items(db)
    assert items[0]["title"] == "New title"
    assert items[0]["notes"] == "Bring cleats"


def test_list_filters_by_child(db):
    create_item(db, child="Alice", title="Alice task")
    create_item(db, child="Bob", title="Bob task")
    alice_items = list_items(db, child="Alice")
    assert len(alice_items) == 1
    assert alice_items[0]["child"] == "Alice"


def test_list_excludes_completed_by_default(db):
    item_id = create_item(db, child="Alice", title="Done task")
    complete_item(db, item_id)
    assert list_items(db) == []
    assert len(list_items(db, include_completed=True)) == 1


def test_dedup_email_items(db):
    create_item(db, child="Alice", title="Game", due_date="2026-05-10", source="email")
    assert item_exists_for_email(db, "Alice", "Game", "2026-05-10") is True
    assert item_exists_for_email(db, "Bob", "Game", "2026-05-10") is False


def test_delete_item(db):
    item_id = create_item(db, child="Alice", title="Delete me")
    assert delete_item(db, item_id) is True
    assert list_items(db, include_completed=True) == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_items.py -v
```

Expected: `ModuleNotFoundError: No module named 'school_dashboard.db'`

- [ ] **Step 3: Create `school_dashboard/db.py`**

```python
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Create items table and indexes if they don't exist."""
    conn = _connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            child        TEXT NOT NULL,
            title        TEXT NOT NULL,
            due_date     TEXT,
            type         TEXT NOT NULL DEFAULT 'assignment',
            source       TEXT NOT NULL DEFAULT 'manual',
            completed    INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
            notes        TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_items_child ON items(child);
        CREATE INDEX IF NOT EXISTS idx_items_due   ON items(due_date);
        CREATE INDEX IF NOT EXISTS idx_items_done  ON items(completed);
    """)
    conn.commit()
    conn.close()


def create_item(
    db_path: str,
    child: str,
    title: str,
    type: str = "assignment",
    source: str = "manual",
    due_date: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    """Insert a new item and return its id."""
    conn = _connect(db_path)
    cursor = conn.execute(
        "INSERT INTO items (child, title, type, source, due_date, notes)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (child, title, type, source, due_date, notes or None),
    )
    conn.commit()
    item_id = cursor.lastrowid
    conn.close()
    return item_id


def update_item(db_path: str, item_id: int, **kwargs) -> bool:
    """Partial update. Pass completed=True/False to also set/clear completed_at."""
    updatable = {"child", "title", "type", "due_date", "notes"}
    fields: dict = {k: v for k, v in kwargs.items() if k in updatable}

    if "completed" in kwargs:
        fields["completed"] = 1 if kwargs["completed"] else 0
        fields["completed_at"] = (
            datetime.now().isoformat() if kwargs["completed"] else None
        )

    if not fields:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [item_id]
    conn = _connect(db_path)
    cursor = conn.execute(
        f"UPDATE items SET {set_clause} WHERE id = ?", values
    )
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def complete_item(db_path: str, item_id: int) -> bool:
    """Mark item completed and record timestamp."""
    return update_item(db_path, item_id, completed=True)


def list_items(
    db_path: str,
    child: Optional[str] = None,
    include_completed: bool = False,
) -> list[dict]:
    """Return items sorted: items with due_date first (ASC), then undated by created_at."""
    if not Path(db_path).exists():
        return []
    conditions: list[str] = []
    params: list = []
    if child:
        conditions.append("child = ?")
        params.append(child)
    if not include_completed:
        conditions.append("completed = 0")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    conn = _connect(db_path)
    rows = conn.execute(
        f"SELECT * FROM items {where}"
        " ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, created_at",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_item(db_path: str, item_id: int) -> bool:
    """Delete an item. Returns True if a row was removed."""
    conn = _connect(db_path)
    cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def item_exists_for_email(
    db_path: str, child: str, title: str, due_date: Optional[str]
) -> bool:
    """True if an email-sourced item with this (child, title, due_date) already exists."""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT 1 FROM items"
        " WHERE child = ? AND title = ? AND due_date IS ? AND source = 'email'",
        (child, title, due_date),
    ).fetchone()
    conn.close()
    return row is not None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_items.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add school_dashboard/db.py tests/test_items.py
git commit -m "feat: add items table and db helpers"
```

---

## Task 2: Flask API routes

**Files:**
- Modify: `web/app.py` (add 4 routes after the existing `/api/chat` route)
- Create: `tests/test_api_items.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_api_items.py`:

```python
import os
import pytest
from web.app import app as flask_app


@pytest.fixture
def client(tmp_path):
    db = str(tmp_path / "test.db")
    flask_app.config["TESTING"] = True
    os.environ["SCHOOL_DB_PATH"] = db
    with flask_app.test_client() as c:
        yield c
    os.environ.pop("SCHOOL_DB_PATH", None)


def test_get_items_empty(client):
    r = client.get("/api/items")
    assert r.status_code == 200
    assert r.get_json() == {"items": []}


def test_post_item_creates(client):
    r = client.post("/api/items", json={
        "child": "Alice", "title": "Soccer practice", "type": "extracurricular"
    })
    assert r.status_code == 201
    data = r.get_json()
    assert data["title"] == "Soccer practice"
    assert data["source"] == "manual"
    assert data["completed"] == 0


def test_patch_item_completes(client):
    r = client.post("/api/items", json={"child": "Alice", "title": "Test"})
    item_id = r.get_json()["id"]
    r2 = client.patch(f"/api/items/{item_id}", json={"completed": True})
    assert r2.status_code == 200
    r3 = client.get("/api/items?include_completed=1")
    items = r3.get_json()["items"]
    assert items[0]["completed"] == 1
    assert items[0]["completed_at"] is not None


def test_patch_item_edits(client):
    r = client.post("/api/items", json={"child": "Alice", "title": "Original"})
    item_id = r.get_json()["id"]
    r2 = client.patch(f"/api/items/{item_id}", json={"title": "Updated", "notes": "New notes"})
    assert r2.status_code == 200
    items = client.get("/api/items").get_json()["items"]
    assert items[0]["title"] == "Updated"
    assert items[0]["notes"] == "New notes"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_api_items.py -v
```

Expected: `FAILED` — routes don't exist yet (`404`)

- [ ] **Step 3: Add routes to `web/app.py`**

Insert the following four routes after the existing `api_chat` function (before `if __name__ == "__main__":`):

```python
@app.route("/api/items", methods=["GET"])
def api_items_list():
    child = request.args.get("child") or None
    include_completed = request.args.get("include_completed", "0") == "1"
    try:
        from school_dashboard.db import init_db, list_items
        init_db(DB_PATH)
        items = list_items(DB_PATH, child=child, include_completed=include_completed)
        return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/items", methods=["POST"])
def api_items_create():
    data = request.get_json(silent=True) or {}
    child = (data.get("child") or "").strip()
    title = (data.get("title") or "").strip()
    if not child or not title:
        return jsonify({"error": "child and title are required"}), 400
    try:
        from school_dashboard.db import init_db, create_item, list_items
        init_db(DB_PATH)
        item_id = create_item(
            DB_PATH,
            child=child,
            title=title,
            type=data.get("type", "assignment"),
            source="manual",
            due_date=data.get("due_date") or None,
            notes=data.get("notes") or None,
        )
        items = list_items(DB_PATH, include_completed=True)
        item = next((i for i in items if i["id"] == item_id), {"id": item_id})
        return jsonify(item), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/items/<int:item_id>", methods=["PATCH"])
def api_items_update(item_id):
    data = request.get_json(silent=True) or {}
    allowed = {"child", "title", "type", "due_date", "notes", "completed"}
    kwargs = {k: v for k, v in data.items() if k in allowed}
    if not kwargs:
        return jsonify({"error": "no valid fields provided"}), 400
    try:
        from school_dashboard.db import init_db, update_item
        init_db(DB_PATH)
        changed = update_item(DB_PATH, item_id, **kwargs)
        if not changed:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def api_items_delete(item_id):
    try:
        from school_dashboard.db import init_db, delete_item
        init_db(DB_PATH)
        deleted = delete_item(DB_PATH, item_id)
        if not deleted:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_api_items.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Run full test suite**

```bash
pytest -v
```

Expected: all previously passing tests still pass + 4 new ones

- [ ] **Step 6: Commit**

```bash
git add web/app.py tests/test_api_items.py
git commit -m "feat: add items CRUD API routes"
```

---

## Task 3: Manage tab UI

**Files:**
- Modify: `web/templates/index.html`

The changes are three insertions into the existing file. Make them one at a time.

### 3a — Add the tab button

- [ ] **Step 1: Insert Manage button in the tab bar**

In `web/templates/index.html`, find:

```html
  <button data-tab="chat">&#128172; Chat</button>
```

Replace with:

```html
  <button data-tab="manage">&#9989; Manage</button>
  <button data-tab="chat">&#128172; Chat</button>
```

### 3b — Add the Manage panel

- [ ] **Step 2: Insert the Manage panel div**

Find:

```html
<div class="tab-panel" id="chat-panel">
```

Insert the following block immediately before it:

```html
<div class="tab-panel" id="manage-panel">
  <div id="manage-subtabs" style="display:flex;gap:4px;padding:8px 10px;background:#12141e;border-bottom:1px solid #1e2130;flex-wrap:wrap;align-items:center">
    <button class="subtab active" data-child="all" onclick="setChild(this,'all')">All</button>
    <button id="add-item-btn" onclick="toggleAddForm()" style="margin-left:auto;padding:6px 14px;background:#1a2a1a;color:#4ade80;border:1px solid #2a3a2a;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">+ Add Item</button>
  </div>
  <form id="add-form" style="display:none;gap:8px;padding:10px;background:#0f1117;border-bottom:1px solid #1e2130;flex-wrap:wrap;align-items:center" onsubmit="submitItem(event)">
    <input id="f-title" required placeholder="Title" style="flex:2;min-width:150px;background:#1a1d27;color:#e6e8ef;border:1px solid #252938;border-radius:8px;padding:8px 12px;font-size:13px">
    <select id="f-child" required style="flex:1;min-width:100px;background:#1a1d27;color:#e6e8ef;border:1px solid #252938;border-radius:8px;padding:8px 12px;font-size:13px">
      <option value="">Child...</option>
    </select>
    <select id="f-type" style="flex:1;min-width:110px;background:#1a1d27;color:#e6e8ef;border:1px solid #252938;border-radius:8px;padding:8px 12px;font-size:13px">
      <option value="assignment">Assignment</option>
      <option value="extracurricular">Extracurricular</option>
      <option value="event">Event</option>
    </select>
    <input id="f-due" type="date" style="flex:1;min-width:120px;background:#1a1d27;color:#e6e8ef;border:1px solid #252938;border-radius:8px;padding:8px 12px;font-size:13px">
    <input id="f-notes" placeholder="Notes (optional)" style="flex:2;min-width:150px;background:#1a1d27;color:#e6e8ef;border:1px solid #252938;border-radius:8px;padding:8px 12px;font-size:13px">
    <input id="f-editing-id" type="hidden" value="">
    <button type="submit" style="padding:8px 16px;background:#4f8ef7;color:#fff;border:0;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">Save</button>
    <button type="button" onclick="cancelAddForm()" style="padding:8px 12px;background:#1a1d27;color:#7a8099;border:1px solid #252938;border-radius:8px;font-size:13px;cursor:pointer">Cancel</button>
  </form>
  <div id="items-list" style="flex:1;overflow-y:auto;background:#0f1117;min-height:0"></div>
</div>
```

### 3c — Add the Manage tab JavaScript

- [ ] **Step 3: Insert Manage JS before `</script>`**

Find the closing `</script>` tag at the end of the file. Insert the following block immediately before it:

```javascript
// ── Manage tab ──────────────────────────────────────────────
const subtabStyle='padding:6px 14px;border:0;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;';
let mgActiveChild='all';
let mgAllItems=[];
let mgShowCompleted=false;

function urgencyDot(due){
  if(!due)return '<span style="width:10px;height:10px;border-radius:50%;background:#3a3d4e;display:inline-block;margin-right:8px;flex-shrink:0"></span>';
  const d=new Date(due+'T00:00:00'),t=new Date();t.setHours(0,0,0,0);
  const diff=Math.round((d-t)/86400000);
  if(diff<0)return '<span style="width:10px;height:10px;border-radius:50%;background:#ef4444;display:inline-block;margin-right:8px;flex-shrink:0" title="Overdue"></span>';
  if(diff===0)return '<span style="width:10px;height:10px;border-radius:50%;background:#f59e0b;display:inline-block;margin-right:8px;flex-shrink:0" title="Today"></span>';
  if(diff<=7)return '<span style="width:10px;height:10px;border-radius:50%;background:#3b82f6;display:inline-block;margin-right:8px;flex-shrink:0" title="Soon"></span>';
  return '<span style="width:10px;height:10px;border-radius:50%;background:#3a3d4e;display:inline-block;margin-right:8px;flex-shrink:0"></span>';
}

function urgencyLabel(due){
  if(!due)return '';
  const d=new Date(due+'T00:00:00'),t=new Date();t.setHours(0,0,0,0);
  const diff=Math.round((d-t)/86400000);
  if(diff<0)return '<span style="font-size:11px;padding:2px 7px;background:#3a1a1f;color:#ef4444;border-radius:5px;font-weight:600;margin-left:6px">OVERDUE</span>';
  if(diff===0)return '<span style="font-size:11px;padding:2px 7px;background:#3a2a0a;color:#f59e0b;border-radius:5px;font-weight:600;margin-left:6px">TODAY</span>';
  if(diff===1)return '<span style="font-size:11px;padding:2px 7px;background:#1a2030;color:#3b82f6;border-radius:5px;font-weight:600;margin-left:6px">TOMORROW</span>';
  return '';
}

async function loadManageItems(){
  try{
    const r=await fetch('/api/items?include_completed='+(mgShowCompleted?'1':'0'));
    const d=await r.json();
    mgAllItems=d.items||[];
    renderSubtabs();
    renderItemList();
  }catch(e){
    document.getElementById('items-list').innerHTML='<div style="color:#ff9aa2;padding:20px">Failed to load items: '+e.message+'</div>';
  }
}

function renderSubtabs(){
  const children=[...new Set(mgAllItems.map(i=>i.child))].sort();
  const sel=document.getElementById('f-child');
  sel.innerHTML='<option value="">Child...</option>'+children.map(c=>'<option>'+c+'</option>').join('');
  const bar=document.getElementById('manage-subtabs');
  const addBtn=document.getElementById('add-item-btn');
  bar.innerHTML='';
  ['all',...children].forEach(c=>{
    const b=document.createElement('button');
    b.textContent=c==='all'?'All':c;
    b.dataset.child=c;
    b.onclick=()=>setChild(b,c);
    b.style.cssText=subtabStyle+(mgActiveChild===c?'background:#4f8ef7;color:#fff':'background:#1a1d27;color:#7a8099');
    bar.appendChild(b);
  });
  bar.appendChild(addBtn);
}

function renderItemList(){
  const list=document.getElementById('items-list');
  const visible=mgActiveChild==='all'?mgAllItems:mgAllItems.filter(i=>i.child===mgActiveChild);
  const active=visible.filter(i=>!i.completed);
  const done=visible.filter(i=>i.completed);
  if(!visible.length){
    list.innerHTML='<div style="color:#5a6076;text-align:center;padding:40px 20px;font-size:14px">No items yet. Click + Add Item to get started.</div>';
    return;
  }
  list.innerHTML=active.map(itemRow).join('')+
    (done.length?'<div id="show-completed-row" style="padding:8px 14px;font-size:12px;color:#4f8ef7;cursor:pointer;text-align:center;border-top:1px solid #1a1d27" onclick="toggleCompleted()">'+
    (mgShowCompleted?'Hide completed ('+done.length+')':'Show completed ('+done.length+')')+'</div>':'')+
    (mgShowCompleted?done.map(completedRow).join(''):'');
}

function itemRow(item){
  const notes=item.notes?'<div style="font-size:12px;color:#7a8099;margin-top:3px">'+escHtml(item.notes)+'</div>':'';
  const meta='<div style="font-size:12px;color:#7a8099;margin-top:2px">'+escHtml(item.child)+(item.due_date?' · '+item.due_date.slice(0,10):'')+' · '+escHtml(item.source)+'</div>';
  return '<div style="display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #1a1d27">'+
    urgencyDot(item.due_date)+
    '<div style="flex:1;min-width:0"><div style="font-size:14px;color:#e6e8ef;font-weight:600">'+escHtml(item.title)+urgencyLabel(item.due_date)+'</div>'+meta+notes+'</div>'+
    '<button onclick="doneItem('+item.id+')" style="padding:4px 10px;background:#1a2a1a;color:#4ade80;border:1px solid #2a3a2a;border-radius:6px;font-size:12px;cursor:pointer;white-space:nowrap">✓ Done</button>'+
    '<button onclick="editItem('+item.id+')" style="padding:4px 8px;background:#1a1d27;color:#7a8099;border:1px solid #252938;border-radius:6px;font-size:12px;cursor:pointer">✎</button>'+
    '</div>';
}

function completedRow(item){
  return '<div style="display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #1a1d27;opacity:0.4">'+
    '<span style="width:10px;height:10px;border-radius:50%;background:#3a3d4e;display:inline-block;margin-right:8px;flex-shrink:0"></span>'+
    '<div style="flex:1"><div style="font-size:14px;color:#7a8099;text-decoration:line-through">'+escHtml(item.title)+'</div>'+
    '<div style="font-size:12px;color:#5a6076">'+escHtml(item.child)+' · Completed</div></div>'+
    '<span style="font-size:11px;padding:2px 7px;background:#1a1d27;color:#5a6076;border-radius:5px">DONE</span></div>';
}

function escHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function setChild(btn,child){
  mgActiveChild=child;
  renderSubtabs();
  renderItemList();
}

function toggleCompleted(){mgShowCompleted=!mgShowCompleted;loadManageItems();}

function toggleAddForm(){
  const f=document.getElementById('add-form');
  const showing=f.style.display==='flex';
  if(showing){cancelAddForm();}
  else{f.style.display='flex';document.getElementById('f-editing-id').value='';document.getElementById('f-title').value='';document.getElementById('f-due').value='';document.getElementById('f-notes').value='';document.getElementById('f-title').focus();}
}

function cancelAddForm(){
  document.getElementById('add-form').style.display='none';
  document.getElementById('f-editing-id').value='';
}

function editItem(id){
  const item=mgAllItems.find(i=>i.id===id);
  if(!item)return;
  const f=document.getElementById('add-form');
  f.style.display='flex';
  document.getElementById('f-editing-id').value=id;
  document.getElementById('f-title').value=item.title||'';
  document.getElementById('f-child').value=item.child||'';
  document.getElementById('f-type').value=item.type||'assignment';
  document.getElementById('f-due').value=(item.due_date||'').slice(0,10);
  document.getElementById('f-notes').value=item.notes||'';
  document.getElementById('f-title').focus();
}

async function doneItem(id){
  await fetch('/api/items/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({completed:true})});
  await loadManageItems();
}

async function submitItem(e){
  e.preventDefault();
  const editingId=document.getElementById('f-editing-id').value;
  const payload={
    child:document.getElementById('f-child').value,
    title:document.getElementById('f-title').value.trim(),
    type:document.getElementById('f-type').value,
    due_date:document.getElementById('f-due').value||null,
    notes:document.getElementById('f-notes').value.trim()||null,
  };
  if(!payload.child||!payload.title)return;
  if(editingId){
    await fetch('/api/items/'+editingId,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  }else{
    await fetch('/api/items',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  }
  cancelAddForm();
  await loadManageItems();
}

// Wire up tab activation
document.querySelectorAll('.tabbar button').forEach(btn=>{
  btn.addEventListener('click',()=>{if(btn.dataset.tab==='manage')loadManageItems();});
});
// ── End Manage tab ───────────────────────────────────────────
```

- [ ] **Step 4: Update `panels` object in existing tab JS**

In `web/templates/index.html`, find:

```javascript
const panels={dashboard:document.getElementById("dashboard-panel"),chat:document.getElementById("chat-panel")};
```

Replace with:

```javascript
const panels={dashboard:document.getElementById("dashboard-panel"),manage:document.getElementById("manage-panel"),chat:document.getElementById("chat-panel")};
```

- [ ] **Step 5: Manual smoke test**

Start the Flask dev server:

```bash
set -a && source config/env && set +a
python -m flask --app web/app run --port 5000
```

Open `http://localhost:5000`. Click "✅ Manage" tab. Verify:
- Tab switches correctly (dashboard iframe hidden, manage panel visible)
- "+ Add Item" shows the form
- Add an item with child, title, and due date — it appears in the list
- ✓ Done marks it complete and it disappears (reappears with "Show completed")
- ✎ Edit repopulates the form, saving updates the item

- [ ] **Step 6: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass (11 total including new ones)

- [ ] **Step 7: Commit**

```bash
git add web/templates/index.html
git commit -m "feat: add Manage tab with add/edit/complete UI"
```

---

## Final: push

```bash
git push
```

---

## Out of Scope (Phase 2)

- Drag-and-drop calendar input for manual entry
- GameChanger integration as `source='gamechanger'`
- Extracurricular extraction from email intel (requires `intel.py` to exist first)
