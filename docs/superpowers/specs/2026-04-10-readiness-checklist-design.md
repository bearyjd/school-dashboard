# Readiness Checklist Design

**Goal:** Give parents a per-child action list showing what each kid still needs to do — assignments not submitted, IXL skills not met, upcoming tests — surfaced on the dashboard and pushed via afternoon/night digests.

**Architecture:** New `readiness.py` module reads existing `school-state.json` + `school.db`. Dashboard renders it server-side. Digest injects a "Checklist" section in afternoon and night modes. No new scraping or data sources needed.

**Tech Stack:** Python, Flask/Jinja2, SQLite, existing `school-state.json` schema.

---

## Components

### `school_dashboard/readiness.py`

Single public function:

```python
def get_checklist(state_path: str, db_path: str, days_ahead: int = 3) -> dict[str, list[dict]]
```

Returns `{child_name: [item, ...]}` where each item has:
- `type`: `"assignment"` | `"ixl"` | `"test"`
- `label`: human-readable description
- `urgency`: `"overdue"` | `"tomorrow"` | `"soon"`
- `detail`: optional string (e.g. IXL score "62/80")

**Data sources:**
- Schoology assignments from `school-state.json` where `due_date` is within `days_ahead` days and `submitted=False` (or submission status unknown)
- IXL teacher-assigned skills from `school-state.json` where `suggested=True` and SmartScore < 80
- `school.db` events of type `TEST` or `QUIZ` within 5 days

**Priority order within each child's list:**
1. Overdue assignments (past due, not submitted)
2. Due tomorrow
3. Due in 2–3 days
4. IXL skills below 80
5. Upcoming tests/quizzes

If a child has no items: returns `[]` (rendered as "All clear").

If submission status is missing from Schoology data: include the item as `urgency="soon"` — err on the side of showing it.

---

### Dashboard Panel

New section in `web/templates/index.html` on the main tab, rendered server-side via Jinja2. Added to the `/` route in `web/app.py` by calling `get_checklist(state_path, db_path)` and passing results to the template.

**Display format:**
```
Readiness
─────────────
Alex
  🔴 Math worksheet — overdue
  🟡 Fractions IXL — 62/80 (due Friday)
  ⚪ Science test — Thursday

Emma
  ✅ All clear
```

No JavaScript required. Refreshes on page reload (same cadence as dashboard sync).

---

### Digest Integration

`digest.py` calls `get_checklist()` in `afternoon` and `night` modes and appends a plain-text "Checklist" section to the ntfy message body.

`morning` mode skips it (kids aren't home yet).

**Night mode** prefixes the section with "Before bed —".

**ntfy format (plain text, no emoji in titles):**
```
Checklist
Alex:
- Math worksheet (overdue)
- Fractions IXL — 62/80
- Science test Thursday
Emma:
- All clear
```

If all children are clear: "Both kids are set for tomorrow."

---

## Error Handling

- Missing `school-state.json`: return empty checklist, log warning
- Missing `school.db`: skip DB-sourced items (tests/quizzes), return assignment + IXL items only
- Missing submission status on assignment: include item, mark urgency based on due date
- Malformed state JSON: catch and return empty, log error

---

## Testing

New `tests/test_readiness.py`. Mock `school-state.json` with known assignments (one overdue, one due tomorrow, one with no submission status) + mock DB with one TEST event + one IXL skill below 80. Assert:
- Correct items returned per child
- Correct priority order
- "All clear" case when no items
- Graceful handling of missing state file
