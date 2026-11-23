# school-dashboard

[Tests](https://github.com/bearyjd/school-dashboard/actions/workflows/tests.yml) · [Docker build and publish](https://github.com/bearyjd/school-dashboard/actions/workflows/docker.yml) · [Contributing](docs/CONTRIBUTING.md) · [Operator runbook](docs/RUNBOOK.md)

All-in-one Docker deployment for family school situational awareness. Scrapes IXL, Schoology, GameChanger and (optionally) PowerSchool, sends a morning digest via ntfy.sh, and serves a Flask web app with a streaming chat interface. (Calendar-PDF parsing and Gmail-based event/fact extraction — `calendar_import.py`/`intel.py` — existed at `v1.0.0` but have since been removed from the codebase; see `CLAUDE.md`'s Architecture section for details, including a known bug this causes in the readiness checklist's tests/quizzes line item.)

**AI features:** Chat and AI-generated digests use an OpenAI-compatible chat completions endpoint. Use OmniRoute, LiteLLM, or another compatible provider or proxy.

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
  digest.py             Build morning digest via an OpenAI-compatible endpoint. Send to ntfy.sh.
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

Dockerfile              Python 3.15 + Node.js 20 + gog + Playwright/Chromium + scrapers + SPA build

docker/
  entrypoint.sh         loads config/env → starts cron → starts Flask on :5000
  crontab               6:00am + 2:30pm weekday syncs; 7am/3:30pm/8:30pm digest jobs; 3:15am backup; 3:45am log rotation
```

## Data Flow

| Time | What happens |
|------|-------------|
| **6:00am** (weekdays) | IXL scrape → SGY scrape → GC scrape → state merge → email intel → dashboard HTML |
| **2:30pm** (weekdays) | IXL + SGY + GC + email re-scrape (catch late homework posts) |
| **7:00am** (daily) | Morning digest — 7-day events + IXL state + facts + GC schedule → AI synthesis → cards → digests DB → ntfy.sh carousel |
| **3:30pm** (weekdays) | Afternoon digest — homework check + cards → digests DB → ntfy.sh |
| **8:30pm** (daily) | Night digest — next-day prep + cards → digests DB → ntfy.sh |
| **Fri 3pm / Sun 7pm** | Weekly digest — week-in-review / week-ahead preview |
| **3:15am** (daily) | Scheduled state backup into `state/backups/` (newest `BACKUP_KEEP`=14 kept) — `sync/run-backup.sh` |
| **3:45am** (daily) | Log rotation of `state/*.log` (size-based, copytruncate, 4 gzip copies) |
| **On demand** | `/api/sync` — trigger per-source sync from the PWA; `/api/sync/meta` for freshness |
| **On demand** | `/api/chat` — query 30-day events + facts + full state via an OpenAI-compatible endpoint |
| **On demand** | `/api/digest/<id>` — retrieve carousel history + toggle card done state |

## Staleness Watchdog

`school-state watchdog` (cron: 8:30am, 4:30pm, 5:05pm and 10:05pm daily, via `sync/run-watchdog.sh`) reads `sync_meta.json` and pushes an ntfy alert ("School: data is stale") when an enabled source (ixl, sgy, gc, powerschool) has no successful sync since the last scheduled one (weekdays 6:00 / 14:30 ET, 90 min grace; no alerts for weekends), when a source has been failing for over 24h, or when it has never synced. `school-state.json` freshness is checked too, but only as a backstop: manual actions also rewrite that file, so a fresh mtime does not prove the scrapers ran. A source seen for the first time (new deploy, or re-enabled) is not reported as "never synced" until one scheduled sync plus grace has passed. The same unresolved source re-alerts at most every 24h (a stale-to-failing label change does not re-alert); one "recovered" push follows when it clears. A failed push (HTTP error, timeout) is not recorded, so the next run retries, and the command exits 2. State lives in `state/watchdog.json`. The schedule constant `SYNC_SCHEDULE` must stay in step with `docker/crontab`. `--dry-run --json` reports without sending. Exit codes: 0 healthy, 1 problems, 2 internal error.

It also acts as a dead-man's switch for digests: when a scheduled digest (morning, afternoon, night, Friday and Sunday weekly) has no row in the `digests` table 90 min after its `docker/crontab` time, it alerts (`Morning Briefing digest: not delivered, ...`). Digests are matched by exact title (a `<title> (fallback)` push does not count as delivered); a digest with no cards writes no row and so also alerts. Disable with `WATCHDOG_DIGESTS_ENABLED=0`.

Silence it: `SOURCE_<NAME>_ENABLED=0` for one source (also pauses its scrape), or `WATCHDOG_ENABLED=0` for the whole watchdog. It skips during maintenance mode (except that maintenance left on for more than 2h raises a `maintenance_stuck` alert) and logs an ERROR and exits 2 when `NTFY_TOPIC` is unset. Sources that are not configured (no GC credentials, PowerSchool absent) are not monitored.

## Configuration

Copy `.env.example` to `config/env` and configure the features you use. The `LITELLM_*` names are retained for compatibility with existing installations.

| Variable | Required | Description |
|----------|----------|-------------|
| `LITELLM_URL` | For AI features | Server root, such as `http://omniroute.example.invalid:20128`. The app appends `/v1/chat/completions`. |
| `LITELLM_API_KEY` | For authenticated AI endpoints | Bearer key issued by your provider or proxy |
| `LITELLM_MODEL` | For AI features | Model ID or alias offered by your endpoint |
| `IXL_EMAIL` | Yes | IXL student login email |
| `IXL_PASSWORD` | Yes | IXL student login password |
| `SGY_EMAIL` | Yes | Schoology parent login email |
| `SGY_PASSWORD` | Yes | Schoology parent login password |
| `SGY_BASE_URL` | For Schoology | Base URL used by the Schoology scraper |
| `SGY_PORTAL_URL` | No | Browser-facing Schoology origin for assignment links, such as `https://portal.example.invalid` |
| `SGY_SCHOOL_NID` | Yes | Schoology school network ID |
| `SCHOOL_DOMAINS` | No | Comma-separated sender domains for school email. The default is `schoology.com,ccsend.com`. |
| `DASHBOARD_BASE` | No | Public dashboard origin, such as `https://school.example.invalid`. Set it to include dashboard links in notifications and automatically allow the origin’s host. |
| `GOG_ACCOUNT` | Yes | Gmail account for gog OAuth |
| `NTFY_TOPIC` | For notifications | Private ntfy.sh topic. Leave it blank until you have a topic for your deployment. |
| `SCHOOL_EMAIL_ACCOUNT` | Yes | Gmail account for school email intel |
| `PS_USERNAME` / `PS_PASSWORD` / `PS_BASE_URL` | No | PowerSchool login for the optional `powerschool` scraper (or `~/.powersched/.env`, `KEY=VALUE`, chmod 600; create `/root/.powersched` on the host first or Docker creates it root-owned and empty). `PS_BASE_URL` is required when PowerSchool is used (missing / late / due-soon work). Source is skipped cleanly when the CLI or credentials are absent. Pause with `SOURCE_POWERSCHOOL_ENABLED=0`. |
| `SYNC_TOKEN` | No | Shared secret for `POST /api/sync`. Generate: `python3 -c "import secrets; print(secrets.token_hex(16))"` |
| `GC_EMAIL` / `GC_PASSWORD` | No | GameChanger credentials. Required for headless self-healing — `gc token-refresh` uses Playwright + auto-OTP via gog Gmail to recover when the token expires. |
| `GC_TOKEN` | No | Optional bearer token from browser DevTools. Only useful for first-run bootstrap; subsequent refreshes write a fresh token to `~/.gc/.env` automatically. |
| `GC_TEAM_MAP` | No | Map team IDs to child names: `"teamid:Avery,teamid2:Casey"` |
| `TWA_PACKAGE_NAME` | No | Android app package name. Set it to the package name of your signed APK for TWA domain verification. |
| `TWA_CERT_FINGERPRINT` | No | Android APK signing fingerprint for TWA domain verification |

Existing deployments can set `DASHBOARD_BASE`, add their school’s sender domains to `SCHOOL_DOMAINS`, and set `TWA_PACKAGE_NAME` to the installed APK’s package name. Set `color` for each child to preserve colors after reordering the roster. Add aliases with lowercase keys to `name_aliases` in your private `config/school-dashboard.json`.

Relative Schoology links use `SGY_PORTAL_URL` or the configured scraper tenant
origin. The dashboard also reads the origin from the mounted scraper settings
when it is absent from the environment.

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
| `/api/chat` | POST | Chat through the configured endpoint (JSON: `message`, `history`) |
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

## Development and CI

Use Python 3.15 and Node.js 26 to match GitHub Actions. From the repository root,
initialize the four pinned scraper submodules (IXL, Schoology, GameChanger, and
PowerSchool), including when working from an existing clone.

```bash
git submodule update --init --recursive
```

Run the commands for each [Tests workflow](.github/workflows/tests.yml) job from
the repository root. Create and activate a fresh virtual environment before each
Python job.

```bash
ci_venv="$(mktemp -d)/venv"
python3.15 -m venv "$ci_venv"
source "$ci_venv/bin/activate"
```

### Python dependency lock (`lock-check`)

```bash
pip install -c requirements.lock ".[server]" \
  -e "vendor/ixl-scrape[browser]" \
  -e vendor/schoology-scrape \
  -e vendor/gc \
  -e "vendor/powerschool-scrape[browser]"
pip check
```

### Python tests (`pytest`)

```bash
pip install -e ".[server]" pytest pyyaml
pip install --no-deps -e vendor/ixl-scrape -e vendor/schoology-scrape \
  -e vendor/gc -e vendor/powerschool-scrape
VENDOR_PINS_STRICT=1 pytest tests/
```

This includes the synthetic end-to-end pipeline test and excludes opt-in live
tests. Run live contract tests with `LIVE_E2E=1 pytest -m live tests/e2e/live -q`
after configuring live credentials.

### SPA lint, build, unit tests, and bundle budget (`spa`)

```bash
(
  cd web/spa
  npm ci
  npm run lint
  npm run build
  npm test
  npm run check-bundle
)
```

For local UI development, run `npm --prefix web/spa run dev` after installing
dependencies. Vite serves `http://localhost:5173/app/` and proxies API calls to
Flask on port 5000.

### SPA browser tests (`e2e`)

```bash
pip install -e ".[server]"
(
  cd web/spa
  npm ci
  npm run build
  npx playwright install --with-deps chromium
  npm run e2e
)
```

Playwright starts the synthetic QA server and runs screen and accessibility
checks. Open the relevant [Tests run](https://github.com/bearyjd/school-dashboard/actions/workflows/tests.yml)
and download **e2e-results** under **Artifacts** for test output, screenshots,
and the Playwright report when produced. CI retains the artifact for seven days.

### Docker build and release

Use a clean recursive checkout of the commit you want to reproduce.

```bash
git submodule status --recursive
docker build -t school-dashboard:local .
```

Every submodule status line should start with a space, which means the checked-out
commit matches the parent repository's pin. Image-affecting pull requests run a
build-only check. The **CI gate** in the Tests workflow reports the results of
the required jobs, including browser tests and the Docker build where applicable.
Run the individual commands above to reproduce a failure locally.

To reproduce the `docker-paths` job, run this in a checkout of the PR merge
commit, with its first parent fetched. Output means the Docker build applies.

```bash
git diff --name-only HEAD^1 HEAD -- \
  Dockerfile .dockerignore docker-compose.yml pyproject.toml requirements.lock .gitmodules \
  vendor school_dashboard web docker sync config school-sync.sh \
  .github/workflows/docker.yml .github/workflows/docker-build.yml .github/workflows/tests.yml
```

On `main`, the Docker workflow publishes image-affecting changes to GHCR under
`latest`, a version tag, and `sha-<full-commit-sha>`. Open
the [Docker workflow run](https://github.com/bearyjd/school-dashboard/actions/workflows/docker.yml)
for the commit and check **Build image** and **Verify published image digest**.
The run summary records the verified image digest. Host
rollout happens separately. Use the [operator failure map](docs/RUNBOOK.md#pipeline-failure-map)
to find the next check, and the [rollout checklist](docs/ops/spa-redesign-rollout.md#scripted-deploy-scriptsdeploysh)
for backup, deploy, and rollback commands.

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
