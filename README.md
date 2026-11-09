# school-dashboard

All-in-one Docker deployment for family school situational awareness. Scrapes IXL, Schoology, GameChanger and (optionally) PowerSchool, sends a morning digest via ntfy.sh, and serves a Flask web app with a streaming chat interface. (Calendar-PDF parsing and Gmail-based event/fact extraction — `calendar_import.py`/`intel.py` — existed at `v1.0.0` but have since been removed from the codebase; see `CLAUDE.md`'s Architecture section for details, including a known bug this causes in the readiness checklist's tests/quizzes line item.)

**External dependency:** LiteLLM proxy at `LITELLM_URL`. Not included — point to your own instance.

## Quick Start

```bash
git clone --recurse-submodules https://github.com/bearyjd/school-dashboard
cd school-dashboard
cp .env.example config/env        # fill in secrets
# (no PDF import; load calendar JSON with `school-state calendar import <file>`)
gog auth add EMAIL                # one-time Google OAuth setup
docker compose up -d
```

## Architecture

```
school_dashboard/       Core Python package
  db.py                 SQLite: `items`, `digests`, `events` tables (inline migrations in `init_db`; `events` rows come from `school-state calendar import`).
  digest.py             Build morning digest via LiteLLM. Send to ntfy.sh.
  email.py              Gmail digest fetch + classification via gog CLI.
  state.py              Aggregate IXL + Schoology + PowerSchool JSON into school-state.json.
  powerschool.py        PowerSchool missing/late/due-soon helpers (digest + quick-check text).
  cli.py                school-state CLI entry point.
  html.py               Render school-state.json → dashboard HTML.

web/
  app.py                Flask app. Routes: /api/chat, /api/agent/inline, /api/items*, /api/sync*, /api/digest*, /app (SPA).
  templates/index.html  Legacy dashboard iframe.
  spa/                  React + TypeScript SPA (Vite build → served at /app): Today, Tonight/Overdue/Missing/Events tables, Calendar, Chat, Sync, Settings (Midnight/Daylight/System theme). Data from /api/overview. The previous views remain at /app/classic/home. Tests: `npm --prefix web/spa test`; screens + axe: `npm --prefix web/spa run e2e`.

sync/
  school-sync.sh        Cron script: IXL → SGY → GC → state → email-sync → HTML → digest (intel step permanently skipped — `intel.py` no longer exists).
  gc-scrape.sh          GameChanger schedule scraper; writes gc-schedule.json.
  run-digest.sh         Wrapper for daily digest jobs (env load, logging, ntfy on failure).
  run-weekly.sh         Wrapper for weekly digest jobs.

vendor/
  ixl-scrape/           git submodule (pip install -e)
  schoology-scrape/     git submodule (pip install -e)
  gc/                   git submodule (pip install -e) — GameChanger CLI
  powerschool-scrape/   git submodule (pip install -e ".[browser]") — PowerSchool CLI (`powerschool`); creds PS_USERNAME/PS_PASSWORD/PS_BASE_URL or ~/.powersched/.env (chmod 600)

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

## Staleness Watchdog

`school-state watchdog` (cron: 8:30am, 4:30pm, 5:05pm and 10:05pm daily, via `sync/run-watchdog.sh`) reads `sync_meta.json` and pushes an ntfy alert ("School: data is stale") when an enabled source (ixl, sgy, gc, powerschool) has no successful sync since the last scheduled one (weekdays 6:00 / 14:30 ET, 90 min grace; no alerts for weekends), when a source has been failing for over 24h, or when it has never synced. `school-state.json` freshness is checked too, but only as a backstop: manual actions also rewrite that file, so a fresh mtime does not prove the scrapers ran. A source seen for the first time (new deploy, or re-enabled) is not reported as "never synced" until one scheduled sync plus grace has passed. The same unresolved source re-alerts at most every 24h (a stale-to-failing label change does not re-alert); one "recovered" push follows when it clears. A failed push (HTTP error, timeout) is not recorded, so the next run retries, and the command exits 2. State lives in `state/watchdog.json`. The schedule constant `SYNC_SCHEDULE` must stay in step with `docker/crontab`. `--dry-run --json` reports without sending. Exit codes: 0 healthy, 1 problems, 2 internal error.

It also acts as a dead-man's switch for digests: when a scheduled digest (morning, afternoon, night, Friday and Sunday weekly) has no row in the `digests` table 90 min after its `docker/crontab` time, it alerts (`Morning Briefing digest: not delivered, ...`). Digests are matched by exact title (a `<title> (fallback)` push does not count as delivered); a digest with no cards writes no row and so also alerts. Disable with `WATCHDOG_DIGESTS_ENABLED=0`.

Silence it: `SOURCE_<NAME>_ENABLED=0` for one source (also pauses its scrape), or `WATCHDOG_ENABLED=0` for the whole watchdog. It skips during maintenance mode and logs an ERROR and exits 2 when `NTFY_TOPIC` is unset. Sources that are not configured (no GC credentials, PowerSchool absent) are not monitored.

## Configuration

Copy `.env.example` to `config/env` and fill in all values:

| Variable | Required | Description |
|----------|----------|-------------|
| `LITELLM_URL` | Yes | LiteLLM proxy full URL including `/v1` (e.g. `https://llm.grepon.cc/v1`) |
| `LITELLM_API_KEY` | Yes | API key for LiteLLM proxy |
| `LITELLM_MODEL` | Yes | Model name (e.g. `school-dash`) |
| `IXL_EMAIL` | Yes | IXL student login email |
| `IXL_PASSWORD` | Yes | IXL student login password |
| `SGY_EMAIL` | Yes | Schoology parent login email |
| `SGY_PASSWORD` | Yes | Schoology parent login password |
| `SGY_BASE_URL` | Yes | Schoology API base URL |
| `SGY_SCHOOL_NID` | Yes | Schoology school network ID |
| `GOG_ACCOUNT` | Yes | Gmail account for gog OAuth |
| `NTFY_TOPIC` | Yes | ntfy.sh topic slug for push notifications |
| `SCHOOL_EMAIL_ACCOUNT` | Yes | Gmail account for school email intel |
| `PS_USERNAME` / `PS_PASSWORD` / `PS_BASE_URL` | No | PowerSchool login for the optional `powerschool` scraper (or `~/.powersched/.env`, `KEY=VALUE`, chmod 600; create `/root/.powersched` on the host first or Docker creates it root-owned and empty). `PS_BASE_URL` is required when PowerSchool is used (missing / late / due-soon work). Source is skipped cleanly when the CLI or credentials are absent. Pause with `SOURCE_POWERSCHOOL_ENABLED=0`. |
| `SYNC_TOKEN` | No | Shared secret for `POST /api/sync`. Generate: `python3 -c "import secrets; print(secrets.token_hex(16))"` |
| `GC_EMAIL` / `GC_PASSWORD` | No | GameChanger credentials. Required for headless self-healing — `gc token-refresh` uses Playwright + auto-OTP via gog Gmail to recover when the token expires. |
| `GC_TOKEN` | No | Optional bearer token from browser DevTools. Only useful for first-run bootstrap; subsequent refreshes write a fresh token to `~/.gc/.env` automatically. |
| `GC_TEAM_MAP` | No | Map team IDs to child names: `"teamid:Ford,teamid2:Jack"` |
| `TWA_CERT_FINGERPRINT` | No | Android APK signing fingerprint for TWA domain verification |

Path overrides (default to `/app/state/` inside Docker):

```
SCHOOL_STATE_PATH, SCHOOL_DB_PATH, SCHOOL_FACTS_PATH,
SCHOOL_EMAIL_DIGEST, SCHOOL_CALENDAR_PDF (legacy, unused by any importer), SCHOOL_GC_PATH,
SCHOOL_SYNC_META_PATH
```

## State Files

All gitignored, stored in `state/`:

| File | Contents |
|------|----------|
| `school.db` | SQLite: `events` table (imported school-calendar rows), `items` table (manual + scraped tasks), `digests` table (carousel history) |
| `facts.json` | Long-term memory: `{subject, fact, source, created_at}` |
| `school-state.json` | Latest IXL + Schoology + PowerSchool aggregate (`powerschool`: per-child missing/late/upcoming) |
| `email-digest.json` | Latest classified Gmail digest |
| `gc-schedule.json` | Latest GameChanger schedule: `{scraped_at, teams: [{team_id, team_name, schedule: [...]}]}` |
| `sync_meta.json` | Per-source scrape timestamps: `{ixl: {last_run, last_result}, sgy: ..., gc: ..., powerschool: ...}` |
| `calendar.pdf` | Legacy path only (`paths.calendar_pdf_path()`, `dry_run.py`); nothing imports a PDF |

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
| `/api/sync` | POST | Trigger on-demand sync (header: `X-Sync-Token`; JSON: `sources` e.g. `"ixl,sgy,powerschool"`, or `"all"`) |
| `/api/sync/status` | GET | Poll sync state (`{running, last_run, last_result, last_sources, last_error}`) |
| `/api/sync/meta` | GET | Per-source sync freshness (`{ixl: {last_run, last_result}, ...}`) |
| `/api/agent/inline` | POST | Inline AI agent for item and sync-source context (JSON: `context_type`, `context_id`, `message`) |
| `/.well-known/assetlinks.json` | GET | Android TWA domain verification |

## One-off Commands

**There is currently no PDF-calendar-import path in this codebase** (`calendar_import.py` was removed). Approved calendar JSON files are loaded with `school-state calendar import <file>`.

```bash
# Force a digest (morning/afternoon/night)
docker compose exec dashboard bash -c \
  'set -a && source /app/config/env && set +a && school-state digest --mode morning'
```

## Development (no Docker)

```bash
pip install -e ".[server]" -e vendor/ixl-scrape -e vendor/schoology-scrape -e vendor/gc -e vendor/powerschool-scrape
playwright install chromium
pytest                            # run all tests
pytest tests/test_db.py -v        # single file
pytest -k "test_name"             # single test
# Run SPA dev server (proxies API to Flask on :5000)
npm --prefix web/spa run dev      # → http://localhost:5173/app/
```

## Tests

1232 tests (`pytest --collect-only -q tests/`, 2026-11-08; the list below is partial). All use mocks — no live credentials needed.

```
tests/test_api_items.py        6 tests  — items API auth + filter combinations
tests/test_digest.py          40 tests  — digest build, ntfy send, GC events, card rendering
tests/test_html.py             6 tests  — dashboard HTML rendering
tests/test_inline_agent.py     6 tests  — inline agent endpoint, context types
tests/test_items.py            9 tests  — items table CRUD
tests/test_llm.py             12 tests  — LiteLLM client, SSE handling, retry
tests/test_readiness.py        8 tests  — readiness checklist generation
tests/test_sync.py            18 tests  — /api/sync auth, concurrency, status, meta, TWA
tests/test_sync_meta.py        8 tests  — sync_meta module read/write, env var path
tests/test_wrapper_scripts.py 11 tests  — wrapper LOGDIR logic, bash syntax,
                                          gc-scrape empty-data detection
```

## Submodule Updates

```bash
git submodule update --remote vendor/ixl-scrape
git submodule update --remote vendor/schoology-scrape
git submodule update --remote vendor/gc
git submodule update --remote vendor/powerschool-scrape
git add vendor/ && git commit -m "chore: update scrapers"
```

## License

Private family tool. Not licensed for redistribution.

## Testing

`pytest tests/` runs everything CI-safe, including a synthetic end-to-end pipeline test. Opt-in live contract tests: `LIVE_E2E=1 pytest -m live tests/e2e/live -q`. Post-deploy smoke check: `scripts/smoke-live.sh` (see `CLAUDE.md`).
