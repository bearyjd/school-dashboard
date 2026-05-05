<!-- Generated: 2026-05-05 | Files scanned: 48 | Token estimate: ~700 -->

# Frontend

## Two surfaces, one repo

| Surface | Mount | Owner | Purpose |
|---|---|---|---|
| React SPA | `/app/`, `/app/*` | `web/spa/` (Vite + TS) | Daily-use PWA — installable to homescreen |
| Static HTML | `/dashboard` | `school_dashboard/templates/dashboard.html` (Jinja2) | Cron-rendered snapshot from `school-state.json` |
| Legacy iframe | `/` | `web/templates/index.html` | Wraps `/dashboard` + chat tab; kept for fallback |

Production traffic uses the SPA. The Jinja dashboard is a deterministic post-sync artifact for sharing or offline.

## React SPA tree (`web/spa/src`)

```
main.tsx → App.tsx
  ├─ <BottomNav>            (Home / Child / Sync / Chat tabs)
  ├─ views/
  │    ├─ Home.tsx          today + tomorrow card stack, quick-add
  │    ├─ Child.tsx         per-child IXL/SGY/GC slice
  │    ├─ Sync.tsx          per-source status + on-demand triggers (uses useSync)
  │    ├─ Chat.tsx          /api/chat with marked-rendered streaming
  │    └─ Settings.tsx      env diagnostics, TWA helpers
  ├─ components/
  │    ├─ ActionCard.tsx    homework / event card with InlineAgent
  │    ├─ ItemSheet.tsx     bottom-sheet item editor
  │    └─ InlineAgent.tsx   contextual chat hooked to /api/agent/inline
  └─ hooks/
       ├─ useSync.ts        POST /api/sync, polls /api/sync/status + /api/sync/meta
       └─ useInlineChat.ts  agent state for InlineAgent
```

## API surface (consumed)

```
src/api/index.ts        wrappers for /api/items, /api/dashboard,
                        /api/digest, /api/sync, /api/chat,
                        /api/agent/inline, /api/calendar, /api/readiness
src/api/types.ts        DTOs (Item, DigestCard, SyncMeta, ...)
src/agent/tools.ts      tool descriptors for the inline-agent prompt
```

## State + auth

- **Server state** lives behind `fetch` wrappers in `api/`; no global store.
- `__SYNC_TOKEN__` is injected into `index.html` at request time by Flask. The SPA reads it once at boot and stamps `X-Sync-Token` on `/api/sync`.
- Source freshness comes from `/api/sync/meta` and is polled while a sync is in-flight.

## PWA / TWA

- `public/manifest.json` declares display, icons, scope `/app/`.
- Service worker registered in `main.tsx` with offline fallback for the shell.
- Android Trusted Web Activity verification via `/.well-known/assetlinks.json` (Flask renders from `TWA_*` env vars).

## Build

```
npm --prefix web/spa run dev      # localhost:5173, proxy → :5000
npm --prefix web/spa run build    # web/spa/dist (served by Flask under /app)
```

The Dockerfile multi-stage build runs the SPA build before the Python image is finalized.

## Static dashboard

`school_dashboard/templates/dashboard.html` is a single dark-theme Jinja2 page rendered by `html.py` from `school-state.json` + `gc-schedule.json`. No JS framework; it is the offline-readable snapshot.
