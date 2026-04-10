<!-- Generated: 2026-04-10 | Files scanned: 34 | Token estimate: ~600 -->

# Architecture

## System Overview

```
[Gmail] ──gog──► email.py ──► intel.py ──► school.db / facts.json
[IXL]  ──ixl──► school-state.json ◄── state.py ──► dashboard.html
[SGY]  ──sgy──►                                          ▲
[PDF]  ──────► calendar_import.py ──► school.db           │
                                                    html.py
                     school.db ──► digest.py ──► ntfy.sh
                     school.db
                     facts.json ──► web/app.py ──► LiteLLM ──► /api/chat
                     school-state.json
```

## Components

| Component | Entry Point | Role |
|-----------|-------------|------|
| CLI scraper orchestration | `school_dashboard/cli.py` | `school-state` command: update/html/show/action/email-sync |
| State aggregation | `school_dashboard/state.py` | Merges IXL + SGY JSON → school-state.json |
| Calendar import | `school_dashboard/calendar_import.py` | PDF → events table (one-time) |
| Email intel | `school_dashboard/intel.py` | LiteLLM extracts events+facts from classified emails |
| Morning digest | `school_dashboard/digest.py` | LiteLLM synthesizes briefing → ntfy.sh push |
| Email fetch | `school_dashboard/email.py` | gog CLI → Gmail → classified email-digest.json |
| Static HTML | `school_dashboard/html.py` | Jinja2 → dashboard.html from school-state.json |
| Flask web app | `web/app.py` | Dashboard iframe + `/api/chat` streaming |
| Cron wiring | `sync/school-sync.sh` | IXL → SGY → state → email-sync → intel → HTML → digest |

## Sync Cycle (6am + 2:30pm weekdays)

```
school-sync.sh
  1. ixl summary --json → school-state.json (IXL half)
  2. sgy summary --json → school-state.json (SGY half)
  3. school-state update → merged school-state.json
  4. school-state email-sync → email-digest.json
  5. intel.process_digest() → school.db / facts.json
  6. school-state html → dashboard.html
  7. digest.py (6am only) → ntfy.sh push
```

## Docker Deployment

```
docker-compose up -d
  └─ dashboard (port 5000)
       ├─ entrypoint.sh: loads config/env → cron → Flask
       ├─ volumes: ./state (DB/JSON), ./config/env, gog-creds, sessions
       └─ vendor/ixl-scrape + vendor/schoology-scrape installed as editable
```
