# On-Demand Sync & Homework Check

**Date:** 2026-04-12  
**Status:** Approved

## Problem

Scrapes run on a fixed cron schedule (6am, 2:30pm). When a child claims homework is done, there's no way to verify without waiting for the next cron window. Parents need a way to trigger a fresh pull immediately — from their phone (ntfy) or from the web dashboard.

---

## Design

### Endpoint: `POST /api/sync`

**Request body (JSON):**

| Field | Values | Default |
|-------|--------|---------|
| `sources` | `ixl`, `sgy`, `gc`, `all` (comma-separated) | `ixl,sgy` |
| `digest` | `quick`, `full`, `none` | `quick` |

**Auth:** `X-Sync-Token` header checked against `SYNC_TOKEN` env var. Returns 401 if token missing/wrong, 501 if `SYNC_TOKEN` not configured.

**Response:** 202 immediately. Sync runs in a background thread.

**Concurrency:** Module-level `threading.Lock`. Returns 409 if a sync is already running.

---

### `GET /api/sync/status`

Returns current sync state — no auth required (no sensitive data):

```json
{
  "running": false,
  "last_run": "2026-04-12T15:04:22",
  "last_result": "ok",
  "last_sources": ["ixl", "sgy"],
  "last_error": null
}
```

---

### Scraper Execution (background thread)

Each source is invoked independently via subprocess. Failures are non-fatal — remaining sources continue.

| Source | Command |
|--------|---------|
| `ixl` | `bash $IXL_CRON` (env var, same as school-sync.sh) |
| `sgy` | `sgy summary --json > $SGY_FILE` |
| `gc` | `bash /app/sync/gc-scrape.sh` |

After all requested scrapers finish:
1. If `ixl` or `sgy` were in sources: `school-state update --ixl-dir $IXL_DIR --sgy-file $SGY_FILE`, then `school-state html`
2. If only `gc` was requested: skip state update (gc writes `gc-schedule.json` directly, not through state pipeline)
3. Digest step (see below)

**IXL invocation:** Inside Docker, `ixl` is installed as a CLI package — invoke as `ixl summary --json` writing output to `$IXL_DIR`. Outside Docker (LXC), use `$IXL_CRON` path. The implementation should mirror how `school-sync.sh` handles the active environment.

---

### Digest Modes

**`quick` (default):** New `build_quick_check(state_path) -> tuple[str, list]` function in `digest.py`. Reads freshly-updated `school-state.json`, formats per-child summary:

```
Ford: IXL 2 remaining (Math), SGY 1 open assignment
Jack: IXL all done, SGY 3 open
Penn: IXL all done, SGY all done
```

No LLM call. Returns `(text, [])` matching existing builder signature. Fires ntfy push with title "Homework Check".

**`full`:** Calls existing `build_afternoon_digest()`. LLM-synthesized, ~10–20s after scrape completes.

**`none`:** No ntfy push. State and dashboard are refreshed silently.

---

### ntfy Action Buttons

`send_ntfy()` gains an optional `actions: list[dict] | None = None` parameter. When provided, serializes to the `X-Actions` header in ntfy format.

The afternoon digest notification includes two action buttons:

```
http, Check Homework, https://school.grepon.cc/api/sync, method=POST,
  body={"sources":"ixl,sgy","digest":"quick"},
  headers.X-Sync-Token=<SYNC_TOKEN>

http, Full Sync, https://school.grepon.cc/api/sync, method=POST,
  body={"sources":"all","digest":"full"},
  headers.X-Sync-Token=<SYNC_TOKEN>
```

The result of a triggered sync arrives as a separate ntfy push when the sync completes, so the phone gets a second notification with the outcome.

---

### Web Dashboard Buttons

Three buttons added to `web/templates/index.html`, rendered in a small control bar:

| Button | Sources | Digest |
|--------|---------|--------|
| Check Homework | `ixl,sgy` | `quick` |
| Full Sync | `all` | `full` |
| Refresh GC | `gc` | `none` |

**UX behavior:**
- Clicking disables the button and shows a spinner
- Polls `GET /api/sync/status` every 3s while `running: true`
- On completion: green checkmark (ok) or red X (error)
- `SYNC_TOKEN` injected into page via a `<meta name="sync-token">` tag from Flask

---

### New Env Var

| Var | Required | Purpose |
|-----|----------|---------|
| `SYNC_TOKEN` | Yes (for this feature) | Shared secret for `/api/sync` auth |

Add to `config/env.example` and `.env.example`.

---

## Files Changed

| File | Change |
|------|--------|
| `web/app.py` | Add `/api/sync` + `/api/sync/status` routes, threading lock, subprocess runner |
| `web/templates/index.html` | Add sync control bar with 3 buttons + polling JS |
| `school_dashboard/digest.py` | Add `build_quick_check()`, add `actions` param to `send_ntfy()` |
| `config/env.example` | Add `SYNC_TOKEN` |
| `.env.example` | Add `SYNC_TOKEN` |
| `CLAUDE.md` | Document new endpoints, env var, quick check function |

---

## Testing

- Unit tests for `build_quick_check()` — all done, some remaining, mixed
- Unit tests for `send_ntfy()` with `actions` — verify `X-Actions` header serialization
- Unit tests for `/api/sync` — 401 on bad token, 409 on concurrent lock, 202 on valid request
- Unit tests for `/api/sync/status` — running/idle states
