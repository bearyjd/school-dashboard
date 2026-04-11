# Context-Aware Digest Deep Links — Design Spec

**Goal:** When a parent taps an ntfy notification, open a full-screen card carousel showing exactly the items referenced in that digest — not a generic dashboard view.

**Architecture:** Digest builders return structured card data alongside the LLM text. Cards are stored server-side in SQLite with a short ID. The ntfy deep link points to `?digest=<id>`. The dashboard detects this param and renders a swipeable carousel instead of the normal tab view.

**Tech Stack:** Python/Flask backend, SQLite storage, vanilla JS + CSS scroll-snap frontend.

---

## Data Flow

1. Each digest builder (`build_morning_digest`, `build_afternoon_digest`, `build_night_digest`, `build_weekly_digest`) returns a `(text, cards)` tuple instead of just text.
2. `cards` is a list of dicts, each representing one actionable item the digest referenced:
   ```python
   {
       "source": "schoology" | "ixl" | "calendar" | "email" | "readiness",
       "child": "Ford",
       "title": "Math Chapter 5 Review",
       "detail": "Pre-Algebra",        # course name, subject, or event type
       "due_date": "2026-04-11",        # ISO date or null
       "url": "https://arlingtondiocese.schoology.com/...",  # direct link if available, else empty
       "done": false
   }
   ```
3. The caller (cron sync script) passes both text + cards to `send_ntfy`.
4. `send_ntfy` writes cards to a `digests` table in SQLite, gets back an 8-char hex ID.
5. Deep link URL: `https://school.grepon.cc/?digest=<id>`
6. ntfy `Click` header and `Actions` header both use this URL.
7. No cards provided (e.g. custom manual notifications) → falls back to static `_DEEP_LINKS` mapping.

## Card Generation per Digest Type

### Morning Briefing
Cards include:
- Each Schoology assignment due today (source=schoology)
- Each IXL subject with remaining > 0 (source=ixl)
- Each email action item due today (source=email)
- Each school calendar event today (source=calendar)
- Each Google Calendar event today (source=calendar)

### Homework Check (Afternoon)
Cards include:
- Each Schoology assignment due today (should be done)
- Each Schoology assignment due tomorrow (should be started)
- Each IXL subject with remaining > 0
- Each email action item due today
- Readiness checklist items if appended

### Night Prep
Cards include:
- Each Schoology assignment due tomorrow
- Each school calendar event tomorrow
- Each Google Calendar event tomorrow
- Each email action item due tomorrow
- Readiness checklist items

### Weekly Digest (Friday/Sunday)
Cards include:
- Each Schoology assignment due in the next 7 days
- Each IXL subject with remaining > 0
- Each school calendar event in the window (3 days for Friday, 7 for Sunday)

## Backend: Database Schema

New table in `school.db`:

```sql
CREATE TABLE IF NOT EXISTS digests (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    cards TEXT NOT NULL   -- JSON array
);
```

Functions in `school_dashboard/db.py`:
- `create_digest(db_path, title, cards) -> str` — generates 8-char hex ID from `os.urandom(4).hex()`, inserts row, returns ID
- `get_digest(db_path, digest_id) -> dict | None` — returns `{id, created_at, title, cards}` or None. Cards is parsed JSON list.
- `mark_digest_card_done(db_path, digest_id, card_index, done) -> bool` — reads cards JSON, updates `done` field at index, writes back. Returns False if digest or index not found.
- `purge_old_digests(db_path, days=7)` — deletes rows where `created_at < now - days`.

## Backend: Digest Builder Changes

Each `build_*_digest` function currently returns `str`. Change return type to `tuple[str, list[dict]]`.

The card list is built from the same data the builder already computes for the LLM prompt. No new data fetching needed — just collect the structured items into cards before formatting them into prompt strings.

Example for `build_morning_digest`:
```python
cards = []
for a in assignments:
    cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                  "detail": a["course"], "due_date": a["due_date"], "url": "", "done": False})
for i in ixl:
    cards.append({"source": "ixl", "child": i["child"], "title": f"{i['subject']}",
                  "detail": f"{i['remaining']} remaining", "due_date": None, "url": "", "done": False})
# ... same pattern for action_items, db_events, cal_events
return _call_litellm(prompt, ...), cards
```

## Backend: send_ntfy Changes

```python
def send_ntfy(topic: str, message: str, title: str = "School",
              cards: list[dict] | None = None, db_path: str | None = None) -> None:
```

- If `cards` is provided and `db_path` is set: call `create_digest(db_path, title, cards)` to get digest ID, build URL as `f"{DASHBOARD_BASE}/?digest={digest_id}"`
- If no cards: fall back to static `_DEEP_LINKS` mapping (current behavior)
- `Click` and `Actions` headers use the computed URL

## Backend: New API Endpoints

### `GET /api/digest/<id>`
Returns:
```json
{
    "id": "a3f8b2c1",
    "title": "Homework Check",
    "created_at": "2026-04-10T15:30:00",
    "cards": [
        {"source": "schoology", "child": "Ford", "title": "Math Ch5 Review",
         "detail": "Pre-Algebra", "due_date": "2026-04-11", "url": "...", "done": false},
        ...
    ]
}
```
Returns 404 if not found or expired.

### `PATCH /api/digest/<id>/cards/<int:index>`
Body: `{"done": true}`
Returns: `{"ok": true}` or 404.

## Frontend: Carousel Mode

### Entry
On page load, check for `?digest=` URL param. If present:
1. Fetch `/api/digest/<id>`
2. If 404 or error → fall back to normal dashboard
3. If success → hide normal dashboard UI, render carousel

### Card Layout
- Each card is a full-viewport-width panel
- Background color tinted by source (light blue for Schoology, light green for IXL, light orange for Email, light purple for Calendar)
- Content: source badge (top-left), child name (top-right), large title centered, detail line below title, due date below detail, direct link button if URL exists
- Large circular checkbox at bottom center

### Navigation
- CSS `scroll-snap-type: x mandatory` on container, `scroll-snap-align: start` on each card
- Native touch swipe works automatically
- Left/right arrow buttons on desktop (absolute positioned on card edges)
- Dot indicators at bottom (filled dot = current card)

### Mark Done
- Tapping checkbox: POST to `/api/digest/<id>/cards/<index>` with `{"done": true}`
- Card gets checkmark overlay, title strikes through, opacity dims to 0.5
- After 500ms delay, auto-scrolls to next undone card
- Tapping again on a done card un-marks it (toggles)

### Progress
- Thin bar at very top: `done_count / total_count` as percentage width
- Text: "2 of 6 done"

### Completion
- When all cards marked done: show centered "All caught up!" with a large checkmark icon
- "View Full Dashboard" button below

### Exit
- "View Full Dashboard" link at bottom of carousel (always visible)
- X button top-right corner
- Both navigate to `https://school.grepon.cc/` (no params)

## Cleanup

- `purge_old_digests(db_path, days=7)` called at the start of each sync run in `school-sync.sh`
- Alternatively, called in `send_ntfy` before creating a new digest (piggyback cleanup)

## Backwards Compatibility

- Callers that don't pass `cards` to `send_ntfy` get the existing static deep link behavior
- The `build_*_digest` return type change requires updating all callers to unpack `(text, cards)`
- The sync script (`school-sync.sh`) and any direct callers need to be updated

## Future Options

### Digest History / Re-surface
If a notification is dismissed without tapping, the `?digest=<id>` deep link is lost. The cards reappear naturally in subsequent digests via the live data sources (Schoology/IXL/calendar). However if reviewing a specific past snapshot is desired:

- `GET /api/digest/latest` — returns the most recently created digest (by `created_at`)
- `GET /api/digest/history` — lists recent digest IDs + titles for a small history view
- A "Recent Digests" section in the dashboard footer linking to the last 3-5 digests

**Note:** Past events (flights, calendar items) age out of future digests naturally via date-window queries. Overdue assignments persist in new digests automatically since Schoology still shows them as incomplete. The `done` checkbox is per-digest-session only — not a source-of-truth for completion.

## Testing

- Unit test: `create_digest` / `get_digest` / `mark_digest_card_done` / `purge_old_digests` in `tests/test_db.py`
- Unit test: each `build_*_digest` returns `(str, list[dict])` with correct card structure in `tests/test_digest.py`
- Unit test: `send_ntfy` with cards creates digest and uses digest URL
- Manual test: send test notification, tap it, verify carousel renders with correct items
