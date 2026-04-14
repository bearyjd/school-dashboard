# school-dashboard

All-in-one Docker deployment for family school situational awareness. Scrapes IXL and Schoology, parses the school calendar PDF, extracts events and facts from Gmail via LiteLLM, sends a morning digest via ntfy.sh, and serves a Flask web app with a streaming chat interface.

**External dependency:** LiteLLM proxy at `LITELLM_URL`. Not included — point to your own instance.

## Quick Start

```bash
git clone --recurse-submodules https://github.com/bearyjd/school-dashboard
cd school-dashboard
cp .env.example config/env        # fill in secrets
# drop 2025-2026 calendar PDF into state/calendar.pdf
gog auth add EMAIL                # one-time Google OAuth setup
docker compose up -d
```

## Architecture

```
school_dashboard/       Core Python package
  db.py                 SQLite schema (events table + facts.json). INSERT OR IGNORE dedup.
  calendar_import.py    Parse school calendar PDF → events DB.
  intel.py              Classify emails via LiteLLM → extract events/facts → DB.
  digest.py             Build morning digest via LiteLLM. Send to ntfy.sh.
  email.py              Gmail digest fetch + classification via gog CLI.
  state.py              Aggregate IXL + Schoology JSON into school-state.json.
  cli.py                school-state CLI entry point.
  html.py               Render school-state.json → dashboard HTML.

web/
  app.py                Flask app. Routes: /api/chat, /api/agent/inline, /api/items*, /api/sync*, /api/digest*, /app (SPA).
  templates/index.html  Legacy dashboard iframe.
  spa/                  React + TypeScript SPA (Vite build → served at /app).

sync/
  school-sync.sh        Cron script: IXL → SGY → GC → state → email-sync → intel → HTML → digest.
  gc-scrape.sh          GameChanger schedule scraper; writes gc-schedule.json.
  run-digest.sh         Wrapper for daily digest jobs (env load, logging, ntfy on failure).
  run-weekly.sh         Wrapper for weekly digest jobs.

vendor/
  ixl-scrape/           git submodule (pip install -e)
  schoology-scrape/     git submodule (pip install -e)
  gc/                   git submodule (pip install -e) — GameChanger CLI

docker/
  Dockerfile            python:3.12-slim + Node 20 + gog v0.12.0 + Playwright/Chromium + scrapers + SPA build
  entrypoint.sh         loads config/env → starts cron → starts Flask on :5000
  crontab               6:00am + 2:30pm weekday syncs; 7am/3:30pm/8:30pm digest jobs
```

## Data Flow

| Time | What happens |
|------|-------------|
| **6:00am** (weekdays) | IXL scrape → SGY scrape → GC scrape → state merge → email intel → dashboard HTML |
| **2:30pm** (weekdays) | IXL + SGY + GC + email re-scrape (catch late homework posts) |
| **7:00am** (daily) | Morning digest — 7-day events + IXL state + facts + GC schedule → LiteLLM → cards → digests DB → ntfy.sh carousel |
| **3:30pm** (weekdays) | Afternoon digest — homework check + cards → digests DB → ntfy.sh |
| **8:30pm** (daily) | Night digest — next-day prep + cards → digests DB → ntfy.sh |
| **Fri 3pm / Sun 7pm** | Weekly digest — week-in-review / week-ahead preview |
| **On demand** | `/api/sync` — trigger per-source sync from the PWA; `/api/sync/meta` for freshness |
| **On demand** | `/api/chat` — query 30-day events + facts + full state via LiteLLM |
| **On demand** | `/api/digest/<id>` — retrieve carousel history + toggle card done state |

## Configuration

Copy `.env.example` to `config/env` and fill in all values:

| Variable | Required | Description |
|----------|----------|-------------|
| `LITELLM_URL` | Yes | LiteLLM proxy base URL (e.g. `http://your-litellm-host:8080`) — never include `/v1` |
| `LITELLM_API_KEY` | Yes | API key for LiteLLM proxy |
| `LITELLM_MODEL` | Yes | Model name (e.g. `cliproxy/claude-sonnet-4-6`) |
| `IXL_EMAIL` | Yes | IXL student login email |
| `IXL_PASSWORD` | Yes | IXL student login password |
| `SGY_EMAIL` | Yes | Schoology parent login email |
| `SGY_PASSWORD` | Yes | Schoology parent login password |
| `SGY_BASE_URL` | Yes | Schoology API base URL |
| `SGY_SCHOOL_NID` | Yes | Schoology school network ID |
| `GOG_ACCOUNT` | Yes | Gmail account for gog OAuth |
| `NTFY_TOPIC` | Yes | ntfy.sh topic slug for push notifications |
| `SCHOOL_EMAIL_ACCOUNT` | Yes | Gmail account for school email intel |
| `SYNC_TOKEN` | No | Shared secret for `POST /api/sync`. Generate: `python3 -c "import secrets; print(secrets.token_hex(16))"` |
| `GC_TOKEN` | No | GameChanger bearer token (from browser DevTools → Authorization header) |
| `GC_EMAIL` / `GC_PASSWORD` | No | GameChanger credentials (Playwright fallback if `GC_TOKEN` unset) |
| `GC_TEAM_MAP` | No | Map team IDs to child names: `"teamid:Ford,teamid2:Jack"` |
| `TWA_CERT_FINGERPRINT` | No | Android APK signing fingerprint for TWA domain verification |

Path overrides (default to `/app/state/` inside Docker):

```
SCHOOL_STATE_PATH, SCHOOL_DB_PATH, SCHOOL_FACTS_PATH,
SCHOOL_EMAIL_DIGEST, SCHOOL_CALENDAR_PDF, SCHOOL_GC_PATH,
SCHOOL_SYNC_META_PATH
```

## State Files

All gitignored, stored in `state/`:

| File | Contents |
|------|----------|
| `school.db` | SQLite: `events` table (calendar + email-extracted events), `items` table (manual + scraped tasks), `digests` table (carousel history) |
| `facts.json` | Long-term memory: `{subject, fact, source, created_at}` |
| `school-state.json` | Latest IXL + Schoology aggregate |
| `email-digest.json` | Latest classified Gmail digest |
| `gc-schedule.json` | Latest GameChanger schedule: `{scraped_at, teams: [{team_id, team_name, schedule: [...]}]}` |
| `sync_meta.json` | Per-source scrape timestamps: `{ixl: {last_run, last_result}, sgy: ..., gc: ...}` |
| `calendar.pdf` | Source PDF (drop in manually each school year) |

## API Endpoints

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/items` | GET | List items (filter by `?child=NAME`, `?include_completed=1`) |
| `/api/items` | POST | Create item (JSON: `child`, `title`, `type`, `due_date`, `notes`) |
| `/api/items/<id>` | PATCH | Update item (partial: `child`, `title`, `type`, `due_date`, `notes`, `completed`) |
| `/api/items/<id>` | DELETE | Delete item |
| `/api/digest/<id>` | GET | Retrieve digest carousel (returns `{id, created_at, title, cards}`) |
| `/api/digest/<id>/cards/<index>` | PATCH | Toggle card done state (JSON: `done: bool`) |
| `/api/dashboard` | GET | Aggregate view (schoology, ixl, email_items) |
| `/api/chat` | POST | Chat with LiteLLM (JSON: `message`, `history`) |
| `/api/readiness` | GET | Get readiness checklist |
| `/api/calendar` | GET | Fetch Google Calendar events |
| `/api/sync` | POST | Trigger on-demand sync (header: `X-Sync-Token`; JSON: `sources` e.g. `"ixl,sgy"`) |
| `/api/sync/status` | GET | Poll sync state (`{running, last_run, last_result, last_sources, last_error}`) |
| `/api/sync/meta` | GET | Per-source sync freshness (`{ixl: {last_run, last_result}, ...}`) |
| `/api/agent/inline` | POST | Inline AI agent for item and sync-source context (JSON: `context_type`, `context_id`, `message`) |
| `/.well-known/assetlinks.json` | GET | Android TWA domain verification |

## One-off Commands

```bash
# Import school calendar PDF into DB
python -m school_dashboard.calendar_import state/calendar.pdf state/school.db

# Force a digest (morning/afternoon/night)
docker compose exec dashboard bash -c \
  'set -a && source /app/config/env && set +a && school-state digest --mode morning'
```

## Development (no Docker)

```bash
pip install -e ".[server]" -e vendor/ixl-scrape -e vendor/schoology-scrape -e vendor/gc
playwright install chromium
pytest                            # run all tests
pytest tests/test_db.py -v        # single file
pytest -k "test_name"             # single test
# Run SPA dev server (proxies API to Flask on :5000)
npm --prefix web/spa run dev      # → http://localhost:5173/app/
```

## Tests

101 tests across 9 files. All use mocks — no live credentials needed.

```
tests/test_db.py              7 tests  — SQLite schema, dedup, facts
tests/test_calendar_import.py 12 tests — PDF parsing, event classification
tests/test_intel.py           4 tests  — LiteLLM extraction, error handling
tests/test_digest.py          40 tests — digest build, ntfy send, GC events, card rendering
tests/test_sync.py            18 tests — /api/sync auth, concurrency, status, meta, TWA
tests/test_sync_meta.py        8 tests — sync_meta module read/write, env var path
tests/test_wrapper_scripts.py  6 tests — run-digest/weekly LOGDIR logic, bash syntax
tests/test_items.py            9 tests — items API CRUD
tests/test_inline_agent.py     6 tests — inline agent endpoint, context types
```

## Submodule Updates

```bash
git submodule update --remote vendor/ixl-scrape
git submodule update --remote vendor/schoology-scrape
git submodule update --remote vendor/gc
git add vendor/ && git commit -m "chore: update scrapers"
```

## License

Private family tool. Not licensed for redistribution.
