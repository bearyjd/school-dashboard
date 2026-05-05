<!-- Generated: 2026-05-05 | Files scanned: 48 | Token estimate: ~900 -->

# Backend

## Flask Routes (`web/app.py`)

```
GET  /                              → legacy dashboard iframe (templates/index.html)
GET  /app, /app/<path>              → React SPA (web/spa/dist) with __SYNC_TOKEN__ injection
GET  /dashboard                     → static dashboard.html from state/

GET  /api/items                     → list items (?child, ?include_completed)
POST /api/items                     → create item
PATCH /api/items/<id>               → partial update
DELETE /api/items/<id>              → delete

GET  /api/digest/<id>               → carousel cards
PATCH /api/digest/<id>/cards/<idx>  → toggle card done

GET  /api/dashboard                 → schoology + ixl + email_items aggregate
POST /api/chat                      → LiteLLM chat (SSE-tolerant)
POST /api/agent/inline              → contextual mini-agent for items / sync sources
GET  /api/readiness                 → readiness checklist
GET  /api/calendar                  → Google Calendar (gog)

POST /api/sync                      → trigger source(s) (header X-Sync-Token; JSON sources)
GET  /api/sync/status               → {running, last_run, last_result, last_sources, last_error}
GET  /api/sync/meta                 → per-source freshness from sync_meta.json

GET  /.well-known/assetlinks.json   → TWA domain verification
```

## CLI (`school_dashboard/cli.py`)

```
school-state update          → state.merge IXL + SGY → school-state.json
school-state html            → html.render → dashboard.html
school-state digest <mode>   → build + send digest (modes: morning|afternoon|night|weekly|quick)
school-state email-sync      → email.fetch_digest → email-digest.json
school-state show / action   → introspection helpers
```

## Core Modules

### `state.py`
- `load_state` / `save_state`, `merge_ixl`, `merge_sgy`
- `prune_stale`, `canonicalize`

### `email.py`
- `fetch_digest(account, days)` — `gog gmail` → classify → email-digest.json
- Buckets: SCHOOL | CHILD_ACTIVITY | FINANCIAL | SKIP

### `digest.py`
- `build_digest_text(events, state, facts, gc_schedule, ...)` — LiteLLM synthesis with plain-text fallback
- `build_quick_check(state_path)` — no-LLM IXL-remaining + open-SGY summary
- `send_ntfy(topic, text)` — ASCII-only Title header
- `build_digest_cards()` — interactive carousel cards stored in `digests` table

### `llm.py`
- `chat_completion(messages, ...)` — wraps LiteLLM. **Always use this**: Omniroute returns SSE for non-streaming requests, plain `resp.json()` breaks.

### `sync_meta.py`
- `read_sync_meta()` / `write_sync_source(source, result)` — atomic per-source freshness
- Path: `SCHOOL_SYNC_META_PATH` (default `state/sync_meta.json`)

### `gcal.py`
- `fetch_calendar_events(account, days)` — `gog calendar list` with module-level TTL cache
- Backs `/api/calendar`

### `readiness.py`
- `build_readiness_checklist(state, ...)` — backs `/api/readiness`

### `html.py`
- Jinja2 → dashboard.html from school-state.json + gc-schedule.json

## Sync Pipeline (`web/app.py:_run_sync_background`)

```
/api/sync POST → token check → background thread:
  for source in {ixl, sgy, gc}:
    run scraper (or skip if not requested)
    write_sync_meta(source, ok|error)
  state.update() → school-state.json
  html.render()  → dashboard.html
  optional digest (mode = quick|full|none)
```

## Environment Variables

```
LITELLM_URL / API_KEY / MODEL    AI proxy + model
IXL_EMAIL / IXL_PASSWORD         IXL student creds
SGY_EMAIL / SGY_PASSWORD / *     Schoology creds + base
GC_EMAIL / GC_PASSWORD           GameChanger creds (auto-OTP via gog)
GOG_ACCOUNT / GOG_KEYRING_PASSWORD  Gmail access for email + GC OTP
NTFY_TOPIC                       push topic
SYNC_TOKEN                       /api/sync auth (optional)
SCHOOL_*_PATH                    state file path overrides
TWA_PACKAGE_NAME / TWA_CERT_FINGERPRINT  Android domain verification
```
