<!-- Generated: 2026-04-10 | Files scanned: 34 | Token estimate: ~700 -->

# Backend

## Flask Routes (`web/app.py`, 145 lines)

```
GET  /              → serves index.html
GET  /dashboard     → serves static dashboard.html (from state/)
POST /api/chat      → streams LiteLLM reply
```

### /api/chat flow
```
request.json["messages"]
  → build_system_prompt()
      → load_upcoming_events(days=30) from school.db
      → load_facts() from facts.json
      → load_state() from school-state.json
      → load_email_digest() from email-digest.json
  → POST LiteLLM /v1/chat/completions (stream=True)
  → stream chunks to client
```

## CLI Entry Points (`school_dashboard/cli.py`, 182 lines)

```
school-state update     → state.py: merge IXL+SGY JSON
school-state html       → html.py: render dashboard
school-state show       → print school-state.json
school-state action     → add/complete/list action items
school-state email-sync → email.py: fetch Gmail digest
school-state email-show → print email-digest.json
```

## Core Modules

### `state.py` (317 lines)
- `load_state(path)` / `save_state(path, state)`
- `merge_ixl(state, ixl_json)` — children, diagnostics, skills, trouble spots
- `merge_sgy(state, sgy_json)` — assignments, grades, announcements
- `prune_stale(state)` — removes old action items
- `canonicalize(state)` — normalizes field names

### `email.py` (389 lines)
- `fetch_digest(account, days)` — calls `gog mail list` + `gog mail get`
- `classify_email(email)` → SCHOOL | CHILD_ACTIVITY | FINANCIAL | SKIP
- `extract_pdf(attachment)` — pdfminer.six text extraction
- Output: `email-digest.json` array of classified email objects

### `intel.py`
- `extract_from_email(email, litellm_url, api_key, model)` → `{events, facts}`
  - Skips non-SCHOOL/CHILD_ACTIVITY/STARRED buckets
  - Strips markdown fences from JSON response
- `process_digest(digest, db_path, facts_path, litellm_url, api_key, model)`
  - Iterates classified emails, calls extract, inserts into DB

### `digest.py`
- `build_digest_text(events, state, facts, litellm_url, api_key, model)` → str
  - Plain-text fallback if LiteLLM fails
- `send_ntfy(topic, message)` — POST to ntfy.sh; ASCII-only Title header

### `calendar_import.py`
- `import_calendar(pdf_path, db_path)` — one-time import
- Detects spaced month headers (A U G U S T)
- Classifies 10 event types: NO_SCHOOL, EARLY_RELEASE, MASS, etc.
- INSERT OR IGNORE dedup

## Database (`school.db`)

See `data.md` for schema.

## Environment Variables

```
LITELLM_URL        LiteLLM proxy base URL
LITELLM_API_KEY    API key for LiteLLM
LITELLM_MODEL      Model name (e.g. claude-sonnet)
SCHOOL_DB_PATH     SQLite path (default: state/school.db)
SCHOOL_FACTS_PATH  facts.json path
SCHOOL_STATE_PATH  school-state.json path
SCHOOL_EMAIL_DIGEST email-digest.json path
GOG_ACCOUNT        Gmail account for gog CLI
NTFY_TOPIC         ntfy.sh topic slug
```
