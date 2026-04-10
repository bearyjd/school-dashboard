# Family Tracker — Design Spec

## Goal

Add a Manage tab to the school dashboard so both parents can track assignments, extracurricular events, and activities in one place — with manual add/edit, mark-complete, and automatic extraction from email intel.

## Architecture

A new `items` table in the existing `school.db` SQLite database serves as the single store for all trackable items. The existing `events` table (calendar + email-extracted school events) is unchanged. Flask gains three new API routes. The frontend gains a third tab (Manage) with All + per-child sub-tabs.

## Data Model

New table added to `school_dashboard/db.py`:

```sql
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child       TEXT NOT NULL,          -- child name or "family" for shared
    title       TEXT NOT NULL,
    due_date    TEXT,                   -- ISO date YYYY-MM-DD or NULL
    type        TEXT NOT NULL,          -- 'assignment' | 'extracurricular' | 'event'
    source      TEXT NOT NULL,          -- 'manual' | 'email' | 'schoology'
    completed   INTEGER DEFAULT 0,      -- 0 or 1
    completed_at TEXT,                  -- ISO datetime or NULL
    notes       TEXT,                   -- optional detail, NULL if empty
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_items_child ON items(child);
CREATE INDEX IF NOT EXISTS idx_items_due   ON items(due_date);
CREATE INDEX IF NOT EXISTS idx_items_done  ON items(completed);
```

DB helper functions added to `db.py`:
- `create_item(db_path, child, title, due_date, type, source, notes) → int` (returns id)
- `update_item(db_path, id, **kwargs)` — partial update, any column
- `complete_item(db_path, id)` — sets completed=1, completed_at=now
- `list_items(db_path, child=None, include_completed=False) → list[dict]`
- `delete_item(db_path, id)`

## intel.py Extension

`process_digest()` gains extracurricular detection. After the existing school-event extraction, a second LiteLLM pass identifies activity emails (sports schedules, practice notices, game announcements, recital/tournament notices, activity newsletters) and calls `create_item()` with `source='email'`.

The prompt instructs LiteLLM to extract:
- `child` — who the item is for (or "family")
- `title` — concise event name
- `due_date` — event date if present
- `type` — 'extracurricular' for sports/music/activities, 'event' for one-off school events
- `notes` — any important detail (location, what to bring, dimensions, etc.)

Before inserting, `process_digest()` checks `SELECT 1 FROM items WHERE child=? AND title=? AND due_date=? AND source='email'` to skip duplicates across syncs. Manual entries are never deduplicated — the user added them intentionally.

## Flask API

Three new routes in `web/app.py`:

```
GET  /api/items              ?child=&include_completed=0   → {items: [...]}
POST /api/items              {child, title, due_date, type, notes}  → {id, ...item}
PATCH /api/items/<int:id>    {title?, due_date?, type?, notes?, completed?}  → {ok: true}
```

All routes read `SCHOOL_DB_PATH` from env (defaults to `/app/state/school.db`).
`PATCH` with `completed=true` also sets `completed_at` to now.

## Frontend — Manage Tab

Changes to `web/templates/index.html`:

### Tab bar
Third button added: `data-tab="manage"`, label `✅ Manage`.

### Manage panel
```
#manage-panel
  .sub-tabs          — All | [child per CHILDREN list from /api/items]
  #add-form          — hidden by default, shown on "+ Add Item" click
    inputs: title, child (select), type (select), due_date, notes
    Save / Cancel buttons
  #items-list
    per item: urgency-dot, title, [notes if present], child·date·source badge, ✓ Done, ✎ Edit
    edit mode: inline form replaces item row
  #show-completed    — "Show completed (N)" toggle link at bottom
```

### Urgency dots (reuse dashboard logic, client-side)
- Red dot — overdue
- Amber dot — today
- Blue dot — within 7 days
- Gray dot — further out

### Notes display
Notes shown inline (small muted text below title) **only when the field has content**. Empty notes field is not rendered.

### Completed items
Hidden by default. "Show completed (N)" link at bottom reveals them, grayed + strikethrough. ✓ Done button absent on completed items.

### Child list
Populated dynamically from the unique `child` values returned by `GET /api/items` — no hardcoded names in the template.

## What Is NOT in Scope (Phase 1)

- Drag-and-drop calendar input (Phase 2)
- GameChanger integration (Phase 2, when added)
- Recurring event support
- Reminders / push notifications (ntfy already handles morning digest)
- Bulk actions

## Tests

New test file `tests/test_items.py`:
- `test_create_and_list_items` — insert, retrieve, verify fields
- `test_complete_item` — mark done, verify completed=1 and completed_at set
- `test_update_item` — partial update of title and notes
- `test_list_filters` — child filter, include_completed filter
- `test_dedup_email_items` — inserting same (child, title, due_date, source='email') twice results in one row
- `test_delete_item` — delete and verify gone

`tests/test_intel.py` gains:
- `test_intel_extracts_extracurricular` — mock LiteLLM returns activity item, verify in items table

`tests/test_api_items.py` (new):
- `test_get_items_empty`
- `test_post_item_creates`
- `test_patch_item_completes`
- `test_patch_item_edits`

## File Changes Summary

| File | Change |
|------|--------|
| `school_dashboard/db.py` | Add `items` table DDL + 5 helper functions |
| `school_dashboard/intel.py` | Add extracurricular extraction pass → items table |
| `web/app.py` | Add GET/POST `/api/items`, PATCH `/api/items/<id>` |
| `web/templates/index.html` | Add Manage tab, sub-tabs, add form, item list with JS |
| `tests/test_items.py` | New — 6 tests for db helpers |
| `tests/test_api_items.py` | New — 4 API tests |
| `tests/test_intel.py` | +1 test for extracurricular extraction |
