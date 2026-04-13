# School Dashboard Mobile App — Design Spec

**Date:** 2026-04-12  
**Status:** Approved

## Overview

A React + Vite PWA served by the existing Flask backend, packaged as a real Android APK via a TWA wrapper. Provides a mobile-first interface for viewing per-child school data, editing homework items, triggering live scrapes, and chatting with an agentic LLM that can take actions on your behalf.

The SPA consumes the existing Flask API exclusively — no new backend endpoints are needed except `GET /api/sync/meta` and `GET /.well-known/assetlinks.json`.

---

## Architecture

### SPA (React + Vite)

Location: `web/spa/` inside the existing repo.

```
web/spa/
  src/
    api/          # typed fetch wrappers for every Flask endpoint
    components/   # shared UI primitives (Button, Card, Sheet, ChatBubble, ActionCard)
    views/        # full-screen pages (Home, Child, Chat, Sync, Settings)
    hooks/        # useItems, useSync, useSyncMeta, useChat, useDashboard
    agent/        # tool-call registry + action confirmation logic
  public/
    manifest.json # PWA manifest (name, icons, display: standalone, start_url: /app)
    sw.js         # service worker — caches app shell, offline fallback page
  index.html
  vite.config.ts
```

### Flask Integration

Two new routes in `web/app.py`:

```python
@app.route("/app", defaults={"path": ""})
@app.route("/app/<path:path>")
def spa(path):
    return send_from_directory("spa/dist", "index.html")

@app.route("/app/assets/<path:filename>")
def spa_assets(filename):
    return send_from_directory("spa/dist/assets", filename)
```

`/.well-known/assetlinks.json` route for TWA domain verification (returns a static JSON file checked into the repo).

### Dockerfile Build Step

```dockerfile
RUN npm --prefix web/spa ci && npm --prefix web/spa run build
```

Added before the pip install step so the bundle is baked into the Docker image.

### TWA (Android APK)

Location: `android/` at repo root — a minimal Bubblewrap/Gradle project.

- Points at `https://school.grepon.cc/app`
- Requires `/.well-known/assetlinks.json` on the server (Flask route, static file)
- GitHub Actions workflow `build-apk.yml` triggered via `workflow_dispatch`
- Produces a signed `.apk` as a downloadable Actions artifact
- No Play Store — sideload via ADB or direct download link

---

## Screens

Bottom navigation bar: Home · Child · Chat · Sync · Settings (gear icon, placeholder).

### Home

Per-child summary cards. Each card shows:
- IXL: remaining skills count
- SGY: open assignments count
- GC: next scheduled event (date, opponent/type)
- Last sync freshness badge per source (e.g. "IXL · 12d ago" in amber if >24h)

Tap a card → navigates to Child view for that child.

### Child

Full item list for one child. Features:
- Filter chips: All · IXL · SGY · GC · Manual
- Status filter: Open / Done
- Tap item → inline edit sheet (title, due date, notes, mark complete)
- Swipe right → mark done (optimistic update, PATCH `/api/items/<id>`)
- FAB → create manual item (POST `/api/items`)

### Chat

Agentic LLM chat interface.

- Message input with voice input button (Web Speech API)
- Quick-action chips pinned above input: "What's due today?", "Check homework", "Sync IXL", "Any GC games this week?"
- LLM responses render as markdown
- Tool-call results render as inline action cards (see Agentic Chat section)
- Conversation history preserved in component state for the session

### Sync

Full-screen sync control.

- Per-source rows: IXL · SGY · GC — each showing last_run timestamp and last_result
- Tap a row → triggers POST `/api/sync {sources: "<source>", digest: "none"}`
- "Sync All" button → triggers all three
- Live status: polls `/api/sync/status` every 3s while running, progress indicator per source
- "Run Quick Check" button → POST `/api/sync {sources: "ixl,sgy", digest: "quick"}`

### Settings (stub)

Placeholder screen. Gear icon in nav. Reserved for future: stored preferences, notification toggles, token management, theme.

---

## Fold Layout

At `min-width: 600px` (Fold inner screen unfolded), Home and Child split into a two-pane layout:
- Left pane: child list / source list
- Right pane: detail view

Implemented with a CSS media query — no JS layout logic needed.

---

## Agentic Chat

The LLM communicates tool calls via a structured JSON block embedded in its response. The frontend parses this client-side and renders a confirmation card before executing write actions. Read actions (queries, syncs) execute immediately and inject the result back into the conversation.

### Tool Registry (v1)

| Tool | Action | Confirmation required |
|------|--------|-----------------------|
| `mark_item_done` | PATCH `/api/items/<id>` `{completed: true}` | Yes |
| `create_item` | POST `/api/items` | Yes |
| `trigger_sync` | POST `/api/sync {sources, digest}` | No — shows inline progress |
| `sync_source` | POST `/api/sync {sources: "<single>", digest: "none"}` → poll → re-fetch dashboard → inject fresh data as LLM context | No — shows inline progress |
| `query_items` | GET `/api/items?child=X` | No — renders inline list card |

### Live Source Pull Flow (`sync_source`)

```
User: "What did Ford actually finish on IXL today?"
LLM:  [tool_call: sync_source, {source: "ixl"}]
App:  → POST /api/sync {sources: "ixl", digest: "none"}
App:  → polls /api/sync/status every 3s until running=false
App:  → GET /api/dashboard (fresh data)
App:  → appends system message to conversation: "Fresh IXL data: <json>"
LLM:  "Ford completed 3 skills today — reading comprehension, fractions…"
```

### Confirmation Card Pattern

```
┌─────────────────────────────────┐
│  Mark done                      │
│  Ford · IXL · Math fractions    │
│                                 │
│  [Cancel]          [Confirm]    │
└─────────────────────────────────┘
```

On Confirm: execute API call, send result back to LLM as a follow-up message. On Cancel: send "User cancelled" to LLM.

---

## Per-Source Sync Metadata

### `state/sync_meta.json`

```json
{
  "ixl": {"last_run": "2026-04-01T06:02:11", "last_result": "ok"},
  "sgy": {"last_run": "2026-04-13T06:03:44", "last_result": "ok"},
  "gc":  {"last_run": "2026-04-13T06:05:01", "last_result": "ok"}
}
```

### Writers

1. `_run_sync_background()` in `web/app.py` — writes per-source entry after each source completes
2. `sync/school-sync.sh` — writes timestamp after each scraper exits 0
3. Path override: `SCHOOL_SYNC_META_PATH` env var (default: `/app/state/sync_meta.json`)

### New Endpoint

`GET /api/sync/meta` — returns `sync_meta.json` contents. No auth required (same as `/api/sync/status`).

### LLM System Prompt Injection

`/api/chat` prepends a `## Data Freshness` section to the system prompt:

```
## Data Freshness
IXL:  last pulled 2026-04-01 06:02 (12 days ago)
SGY:  last pulled 2026-04-13 06:03 (today)
GC:   last pulled 2026-04-13 06:05 (today)
```

This ensures the LLM can accurately answer staleness questions rather than inferring from stale state data.

---

## PWA Configuration

### `manifest.json`

```json
{
  "name": "School Dashboard",
  "short_name": "School",
  "start_url": "/app",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#1a1a2e",
  "icons": [{"src": "/app/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}]
}
```

### Service Worker

- Caches app shell (HTML, JS, CSS) on install
- Network-first for API calls, cache fallback for assets
- Offline page shown if network unavailable and no cache

---

## TWA Domain Verification

Flask serves `GET /.well-known/assetlinks.json` — a static file checked into `web/static/.well-known/assetlinks.json`. This file contains the SHA-256 fingerprint of the APK signing key. Required for Chrome to hide the browser URL bar in TWA mode.

The `android/` Gradle project is generated by Bubblewrap CLI and checked into the repo. The signing keystore is stored as a GitHub Actions secret and injected at build time.

---

## New Backend Changes Summary

| Change | File | Purpose |
|--------|------|---------|
| SPA catch-all route | `web/app.py` | Serve React bundle |
| `GET /api/sync/meta` | `web/app.py` | Per-source freshness for SPA + LLM |
| `GET /.well-known/assetlinks.json` | `web/app.py` | TWA domain verification |
| Data freshness in system prompt | `web/app.py` `/api/chat` | LLM knows when data was last pulled |
| Write `sync_meta.json` on sync | `web/app.py` + `sync/school-sync.sh` | Persist per-source timestamps |
| `npm run build` in Dockerfile | `Dockerfile` | Bake SPA into image |
| `SCHOOL_SYNC_META_PATH` env var | `config/env.example` | Path override |

---

## Out of Scope (this spec)

- Push notifications (ntfy handles that already)
- Offline data editing (read-only offline is sufficient for v1)
- Multi-user / auth (single-family deployment)
- Play Store submission
- Settings screen implementation (stub only)
