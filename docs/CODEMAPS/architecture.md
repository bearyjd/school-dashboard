<!-- Generated: 2026-05-05 | Files scanned: 48 | Token estimate: ~750 -->

# Architecture

## System Overview

```
[Gmail]   ──gog──► email.py ──► email-digest.json
[IXL]     ──ixl──┐
[SGY]     ──sgy──┼──► state.py ──► school-state.json
[GC]      ──gc───┘
[GCal]    ──gog──► gcal.py (cached) ──► /api/calendar

                          digest.py (LiteLLM) ──► ntfy.sh push
                                              ──► digests table (carousel)
                          html.py ─────────────► dashboard.html
                          web/app.py ── /api/* ── React SPA at /app
                                     └─ /api/chat ─ LiteLLM (SSE)
```

## Components

| Component | Entry Point | Role |
|---|---|---|
| CLI orchestration | `school_dashboard/cli.py` | `school-state` command (update / html / digest / email-sync) |
| State aggregation | `school_dashboard/state.py` | Merge IXL + SGY JSON → school-state.json |
| DB schema + items + digests | `school_dashboard/db.py` | SQLite tables (items, digests) |
| Digest | `school_dashboard/digest.py` | morning / afternoon / night / weekly + cards → ntfy.sh carousel |
| Email fetch | `school_dashboard/email.py` | gog → Gmail → email-digest.json |
| LLM client | `school_dashboard/llm.py` | LiteLLM wrapper (handles SSE-for-non-streaming) |
| Google Calendar | `school_dashboard/gcal.py` | gog Calendar fetch with in-process cache |
| Readiness | `school_dashboard/readiness.py` | morning checklist generation |
| Static HTML | `school_dashboard/html.py` | Jinja2 → dashboard.html from school-state.json |
| Sync metadata | `school_dashboard/sync_meta.py` | Per-source last_run/last_result tracking |
| Flask app | `web/app.py` | API + React SPA host |
| React SPA | `web/spa/` | Vite + TS, served at `/app/` |
| Cron orchestrator | `sync/school-sync.sh` | IXL → SGY → GC → state → email → HTML → digest |
| GC scraper | `sync/gc-scrape.sh` | GameChanger schedule scrape with empty-data abort |

> _Aspirational modules not currently committed:_ `intel.py` (LLM event extraction from emails), `calendar_import.py` (one-time PDF → events). Referenced in older docs and `school-sync.sh`; both stub out cleanly when missing.

## Sync Cycle (6am + 2:30pm weekdays)

```
school-sync.sh
  1. ixl assigned --json     → /tmp/ixl/<child>-assigned.json
  2. sgy summary --json      → /tmp/schoology-daily.json
  3. gc token-refresh        → fresh user JWT to ~/.gc/.env (Layer 3)
  4. gc-scrape.sh            → state/gc-schedule.json (Layer 2 aborts on all-empty)
  5. school-state update     → school-state.json
  6. school-state email-sync → email-digest.json (when SCHOOL_EMAIL_ACCOUNT set)
  7. intel.process_digest()  → school.db / facts.json (when intel module present)
  8. school-state html       → dashboard.html
```

## Self-Healing GC Auth (3 layers)

| Layer | Surface | Action |
|---|---|---|
| 1 | `vendor/gc/gc_cli/client.py` `GCClient._get` | Retry-on-401: refresh session via saved Playwright context, persist fresh JWT, retry once. |
| 2 | `sync/gc-scrape.sh` | Sum events; if all teams empty → loud abort + ntfy alert (refuses to overwrite gc-schedule.json). |
| 3 | `sync/school-sync.sh` | Run `gc token-refresh` upfront so cron starts with hot JWT. |

Auto-OTP via gog: `_fetch_gc_otp` polls Gmail for `from:gamechanger-noreply@info.gc.com` codes newer than function-start; fills `input#code`; submits.

## Docker Deployment

```
docker compose up -d
  └─ dashboard (port 5000)
       ├─ entrypoint.sh: env → cron daemon → Flask
       ├─ volumes: ./state ./web ./config/env /root/.{ixl,sgy,gc,config/gogcli}
       └─ vendor/{ixl-scrape, schoology-scrape, gc} pip install -e
```
