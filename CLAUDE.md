# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

All-in-one Docker deployment for the family school dashboard. Scrapes IXL,
Schoology, GameChanger, and (optionally) PowerSchool; aggregates into `school-state.json`; sends
morning/afternoon/night/weekly digests via ntfy; and serves a Flask app + React
SPA with a chat interface. (Calendar-PDF parsing and Gmail-based event/fact
extraction — `calendar_import.py`/`intel.py` — existed at `v1.0.0` but have since
been removed from the codebase; see the Architecture section below before
assuming either capability is present.)

**External dependency:** LiteLLM proxy at `LITELLM_URL` (e.g. `https://llm.grepon.cc/v1`). Not included — point to your own instance.

**Agent-native audit:** see `.agent_native/agent_roadmap.md` for a prioritized list
of gaps (test-collection trap, missing CI test gate, stale docs, missing scraper-
output contract tests, a Flask env-var inconsistency) an autonomous coding agent
should know about before picking up a bug report in this repo.

## Quick Start

```bash
git clone --recurse-submodules <repo>
cp .env.example config/env        # fill in secrets
# drop 2025-2026 calendar PDF into state/calendar.pdf
gog auth add EMAIL                # one-time Google OAuth setup
docker compose up -d
# Access SPA at https://school.grepon.cc/app (install to homescreen for PWA)
```

## Development (no Docker)

```bash
pip install -e ".[server]" -e vendor/ixl-scrape -e vendor/schoology-scrape -e vendor/gc -e vendor/powerschool-scrape
playwright install chromium
pytest tests/                     # run all tests — SCOPE TO tests/, see warning below
pytest tests/test_readiness.py -v # single file
pytest -k "test_name"             # single test
# Run SPA dev server (proxies API to Flask on :5000)
npm --prefix web/spa run dev   # → http://localhost:5173/app/
npm --prefix web/spa run build # tsc -b && vite build
npm --prefix web/spa run lint && npm --prefix web/spa test && npm --prefix web/spa run check-bundle
# Screens (10 routes x 5 widths x 2 themes, screenshots only, no visual diff) + axe (10 routes x 375/1280 px x 2 themes,
# wcag2a/2aa/21a/21aa, serious+critical fail) against synthetic data;
# needs `build` first and `pip install -e ".[server]"`; starts scripts/spa-qa-server.py itself:
npm --prefix web/spa run e2e      # screenshots -> $E2E_SHOTS (default web/spa/test-results/shots)
python3 scripts/spa-qa-server.py --port 5099   # same synthetic server for manual preview at /app/
```

**Run `pytest tests/` from the repo root (the documented command).** `pyproject.toml` now sets `testpaths = ["tests"]`, so bare `pytest` is also safe; the note below explains the original trap, which is fixed.
Verified 2026-07-07: bare `pytest` fails collection with
`ImportPathMismatchError` / `ModuleNotFoundError: No module named 'tests.test_summary'`
because `vendor/ixl-scrape`, `vendor/schoology-scrape`, `vendor/gc`, and `vendor/powerschool-scrape` are each
separate git repos with their own `tests/conftest.py` / `tests/test_*.py`, and
pytest's default rootdir-based collection picks all of them up and collides on the
identical top-level module name `tests`. This repo's `pyproject.toml` does not yet
set `testpaths`, so this is a real trap, not a config mistake on your machine — see
`.agent_native/agent_roadmap.md` item 1 for the one-line fix (add
`testpaths = ["tests"]` to `[tool.pytest.ini_options]`). Also note: `flask` is not a
base dependency — `pip install -e ".[server]"` (the `server` extra) is required
before any test importing `web.app` will collect.

Verified passing command as of 2026-07-07 (after `pip install -e ".[server]"`,
no vendor packages needed for these): `pytest tests/` → **124 passed**. This is
higher than the "101 tests / 7 files" figure quoted lower in this document — that
count predates `test_api_items.py`, `test_readiness.py`, `test_llm.py`, and
`test_inline_agent.py` being added; see the corrected Tests table below.

CI: `.github/workflows/tests.yml` runs `pytest tests/` (job `pytest`), the SPA lint/build/vitest/
bundle-budget checks (job `spa`) and the Playwright screens + axe suite (job `e2e`, non-blocking via
`continue-on-error`). `.github/workflows/docker.yml` only builds and pushes the Docker image on push
to `main`.

## GameChanger calendar-feed (ICS) fallback

`sync/gc-scrape.sh` falls back per team to a calendar feed (`school_dashboard/gc_ics.py`, `school-state gc-ics --team-id ID`)
when `gc summary`/`gc teams` fails or is empty. Feed URLs are SECRETS in `~/.gc/ics-feeds.json` (chmod 600); never log or commit them. See `docs/ops/gc-ics-fallback.md`.

## One-off Commands

> **Correction (2026-07-07):** `school_dashboard.calendar_import`, `query_upcoming_events`,
> `load_facts`, and `build_digest_text` **do not exist in this codebase** — verified via
> `grep -n "^def " school_dashboard/db.py school_dashboard/digest.py`. The commands
> previously documented here referenced a `calendar_import.py`/`intel.py`-era API that
> was removed at some point after `[1.0.0]` without a matching `CHANGELOG.md` entry
> (see `.agent_native/agent_roadmap.md` item 3). Real, current equivalents:

```bash
# Force a digest (bypasses the cron hour check) — current public API is one of
# build_morning_digest / build_afternoon_digest / build_night_digest / build_weekly_digest
# / build_quick_check in school_dashboard/digest.py. Example, morning digest:
set -a && source config/env && set +a
python3 -c "
import os, sys
sys.path.insert(0, '.')
from school_dashboard.digest import build_morning_digest
text = build_morning_digest(
    state_path='state/school-state.json',
    db_path='state/school.db',
    facts_path='state/facts.json',
    litellm_url=os.environ['LITELLM_URL'],
    api_key=os.environ['LITELLM_API_KEY'],
    model=os.environ['LITELLM_MODEL'],
)
print(text)
"

# No-LLM quick check (IXL remaining + open SGY assignments per child):
python3 -c "
import sys; sys.path.insert(0, '.')
from school_dashboard.digest import build_quick_check
text, actions = build_quick_check('state/school-state.json')
print(text)
"
```

**There is currently no PDF-calendar-import path in this codebase** (`calendar_import.py`
was removed; nothing recreates the `events` table it used to populate — see the
`readiness.py` note in Architecture below). If calendar-PDF import is needed again,
it must be rebuilt, not resurrected from a stale doc reference.

Approved school-calendar JSON files in `config/calendars/` are instead loaded with
`school-state calendar import <file> [--dry-run] [--json]` (`school_calendar.py`): it
replaces that calendar's rows (`source = calendar:<calendar_id>`) in the `events` table, and
exits 3 under maintenance mode (`--dry-run` is read-only and always allowed). The app does no PDF parsing.

`school-state schoology reset --sgy-file PATH [--archive-dir DIR] [--max-age-hours 24] [--allow-warnings] [--dry-run] [--json]` (`schoology_reset.py`) archives stale pending schoology action items and the old `schoology` section to `<state>/archive/schoology-<UTC>.json` (0600, verified before the state swap), then rebuilds both from that one fresh `sgy summary --json`. It requires maintenance mode ON and no sync in flight (exit 3 otherwise; `--dry-run` is read-only and always allowed), refuses a scrape that reports `warnings` unless `--allow-warnings`, exits 2 on a missing/stale/empty/malformed scrape or state, 1 on a failed write (state left unchanged), and is idempotent.
Non-schoology items, completed items and all other state are left untouched.

## Architecture

```
school_dashboard/       Core Python package
  db.py                 SQLite: `items` table (manual + scraped tasks) + `digests` table (carousel history). No events table (see note below).
  digest.py             Build morning/afternoon/night/weekly digest text via LiteLLM (build_morning_digest / build_afternoon_digest / build_night_digest / build_weekly_digest). Send to ntfy.sh push. `build_quick_check(state_path)` → no-LLM homework summary (IXL remaining + open SGY assignments per child).
  llm.py                SSE-tolerant OpenAI-compatible chat client (`chat_completion`) shared by digest.py and web/app.py's /api/chat. Every call in this repo's test suite mocks `requests.post` at this boundary — this is the one true "requires an external LLM" seam.
  email.py              Gmail digest fetch + classification.
  gcal.py               Google Calendar event fetch (via `gog`), used by digest.py's `_gcal_events_on`.
  state.py              Aggregate IXL + Schoology + PowerSchool JSON into school-state.json. `update_from_ps_file` keeps previous PowerSchool data (and records a warning in `powerschool_meta`) when the scrape file is missing/stale/empty/malformed or carries `warnings`.
  powerschool.py        PowerSchool helpers: scrape cleaning/age check, `skip_reason()`, deterministic MISSING / LATE / DUE SOON text (`format_block`, `format_counts`) used by digests, quick check and chat.
  cli.py                school-state CLI entry point.
  html.py               Render school-state.json → dashboard HTML.
  readiness.py          Deterministic, pure-function readiness checklist (get_checklist). No LLM. Fully unit-tested (tests/test_readiness.py, 8 tests) — safe for an agent to modify/verify in isolation.
  sync_meta.py           Per-source sync freshness read/write (see "Sync, Staleness & Vendor Model" below).

  **⚠️ `intel.py` and `calendar_import.py` do not exist** — they shipped in `[1.0.0]`
  (see `CHANGELOG.md`) but were removed later without a changelog entry. `sync/
  school-sync.sh` already guards the intel step with `python3 -c "import
  school_dashboard.intel"` and skips cleanly when it's absent — that guard firing is
  expected behavior, not a bug to chase. Consequence: nothing in this codebase creates
  the SQLite `events` table anymore (`calendar_import.py` used to), so
  `readiness.py::get_checklist`'s `SELECT ... FROM events WHERE type IN ('TEST','QUIZ')`
  query fails against a real `school.db` and is silently swallowed by a bare
  `except Exception` — the "tests/quizzes" readiness line item cannot currently
  populate in a real deployment. Treat this as a known, currently-dead code path
  (`.agent_native/agent_roadmap.md` item 3), not a mystery to reverse-engineer.

web/
  app.py                Flask app. Routes: /api/chat, /api/items*, /api/dashboard, /api/readiness, /api/digest/* (carousel), /api/sync, /api/sync/status. **Paths:** every state path resolves per call via `school_dashboard/paths.py` (`SCHOOL_STATE_DIR`, default `/app/state`, plus per-file overrides such as `SCHOOL_DB_PATH`), so `monkeypatch.setenv` works for all routes. Also: `/api/*` writes return 503 while `maintenance.json` is present (`school-state transition maintenance on|off`), except `/api/chat`.
  spa/                  Vite + React + TypeScript SPA (`web/spa/`), data from `GET /api/overview`. Routes (under `/app`): `/` Today, `/tonight` `/overdue` `/missing` `/events` (sortable/filterable tables, state in the URL; manual items can be checked off), `/calendar`, `/chat`, `/sync`, `/settings`, `/classic/*` (the previous Home/Child views, kept as a fallback). Theme: Midnight / Daylight / System, chosen in Settings, stored in `localStorage['school-theme']`, applied as `data-theme` on `<html>` (`src/design/theme.ts`). Service worker cache is `school-v2`. Tests: vitest (`npm test`), Playwright screens + axe (`npm run e2e`, `web/spa/e2e/`; routes `/ /tonight /overdue /missing /events /calendar /settings /chat /sync /classic/home`; screens: 320/375/768/1024/1440 px x both themes, asserting no console errors, failed or unexpected 4xx/5xx requests, or horizontal overflow, and saving screenshots without comparing them; axe: 375 and 1280 px x both themes, impact serious and critical fail, moderate and minor are reported nowhere; CI job `e2e` is non-blocking). Lighthouse (performance >= 90, LCP < 2.5 s, CLS < 0.1) is a manual gate. Rollout/rollback: `docs/ops/spa-redesign-rollout.md`.
  templates/index.html  Legacy dashboard iframe + chat tab (pre-SPA). Uses marked.js for markdown rendering.

sync/
  school-sync.sh        Cron script: IXL → SGY → GC → state → email-sync → HTML → digest (email-intel step permanently skipped — see note above). Runs at 6:00am and 2:30pm weekdays.
  gc-scrape.sh          Scrapes GameChanger for all configured teams; writes gc-schedule.json.

vendor/
  ixl-scrape/           git submodule (pip install -e)
  schoology-scrape/     git submodule (pip install -e)
  gc/                   git submodule (pip install -e) — GameChanger CLI
  powerschool-scrape/   git submodule (pip install -e ".[browser]") — PowerSchool CLI (`powerschool`); creds PS_USERNAME/PS_PASSWORD/PS_BASE_URL or ~/.powersched/.env (chmod 600)

docker/
  Dockerfile            python:3.12-slim + gog v0.12.0 binary + Playwright/Chromium + pip scrapers
  entrypoint.sh         loads config/env → starts cron → starts Flask on :5000
  crontab               6:00am + 2:30pm weekday syncs

**Docker environment:** `TZ=America/New_York` (required for correct digest timing and event display)
```

## Key Data Flows

> **Calendar import and email-intel extraction are not currently implemented**
> (`calendar_import.py` / `intel.py` were removed post-`[1.0.0]` — see Architecture
> note above). The two bullets below describe what actually runs today.

- **GameChanger scrape (each sync, non-fatal):** `gc-scrape.sh` calls `gc teams --json`, iterates team IDs, calls `gc summary --json --team ID` per team, writes merged `gc-schedule.json`. Skipped if `GC_TOKEN` and `GC_EMAIL` are both unset.
- **Morning/afternoon/night/weekly digest (6am/2:30pm/8:30pm/Fri+Sun):** `digest.py`'s `build_morning_digest`/`build_afternoon_digest`/`build_night_digest`/`build_weekly_digest` query IXL+Schoology state + facts.json + gc-schedule.json (no `events` table — see above) → LiteLLM synthesizes → creates `digests` carousel row → ntfy.sh push with deep-link. This is the one genuine LLM-judgment surface in the pipeline: the *prompt wording* was tuned by a human and isn't something an agent should freely rewrite without re-validating tone; the *plumbing* around it (what data gets assembled, formatting, ntfy delivery) is ordinary deterministic code covered by `tests/test_digest.py`.
- **Readiness checklist (on-demand, `/api/readiness`):** `readiness.py::get_checklist` — pure, deterministic, no LLM. Fully unit-tested. The one caveat: its `events`-table query for tests/quizzes always fails and returns empty (see Architecture note) since nothing creates that table anymore.
- **Chat:** `/api/chat` builds system prompt with 30-day events + facts + full state JSON → streams reply from LiteLLM via `school_dashboard/llm.py::chat_completion`.

## API Endpoints

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | 302 to `/app/` (query kept); `?digest=<id>` goes to `/legacy` so ntfy digest links still open the carousel |
| `/legacy` | GET | Pre-SPA dashboard page (retired with the classic view; renders no token and has no sync buttons, it links to Sync in `/app/`) |
| `/api/items` | GET | List items (filter by `?child=NAME`, `?include_completed=1`) |
| `/api/items` | POST | Create item (JSON: `child`, `title`, `type`, `due_date`, `notes`) |
| `/api/items/<id>` | PATCH | Update item (partial: `child`, `title`, `type`, `due_date`, `notes`, `completed`) |
| `/api/items/<id>` | DELETE | Delete item |
| `/api/digest/<id>` | GET | Retrieve digest carousel (returns `{id, created_at, title, cards}`) |
| `/api/digest/<id>/cards/<index>` | PATCH | Toggle card done state (JSON: `done: bool`) |
| `/api/overview` | GET | Normalised items (status overdue/missing/late/tonight/upcoming), events (GameChanger + school calendar) and per-source freshness for the SPA (`?days=1..60`, `?past=1`) |
| `/api/dashboard` | GET | Aggregate view (schoology, ixl, email_items) |
| `/api/chat` | POST | Chat with LiteLLM (JSON: `message`, `history`) |
| `/api/readiness` | GET | Get readiness checklist |
| `/api/calendar` | GET | Fetch Google Calendar events |
| `/api/sync` | POST | Trigger on-demand sync (header: `X-Sync-Token`, compared in constant time; the token is **never served** in HTML/JS/JSON; the SPA keeps a user-entered copy in `localStorage['school-sync-token']`; 10 bad tokens/min per client (`remote_addr`, or the `TRUSTED_PROXY_COUNT`-th `X-Forwarded-For` entry from the right; IPv6 by /64) -> 429 + `Retry-After`, a correct token is never blocked; token must be >= 32 chars; JSON: `sources` e.g. `"ixl,sgy,powerschool"` or `"all"`, `digest` e.g. `"quick"/"full"/"none"`) |
| `/api/sync/status` | GET | Poll sync state (`{running, last_run, last_result, last_sources, last_error}`); `last_error` is generic text, detail only in the server log |
| `/api/csp-report` | POST | CSP report sink: 204, no auth, exempt from maintenance 503, body never read or logged (counts only). `/app/*` and `/legacy` send `Content-Security-Policy-Report-Only` (`school_dashboard/csp.py::CSP_REPORT_ONLY`) |
| `/api/sync/meta` | GET | Per-source sync freshness (`{ixl: {last_run, last_result}, ...}`) |
| `/.well-known/assetlinks.json` | GET | TWA domain verification (fingerprint from `TWA_CERT_FINGERPRINT` env var) |

## State Files (all gitignored, in state/)

| File | Contents |
|------|----------|
| `school.db` | SQLite: `events` table (calendar + email-extracted events), `items` table (manual + scraped tasks), `digests` table (carousel history) |
| `facts.json` | Array of `{subject, fact, source, created_at}` — long-term memory |
| `school-state.json` | Latest IXL + Schoology + PowerSchool aggregate; `powerschool` = `{child: {updated, missing[], late[], upcoming[]}}`, `powerschool_meta` = `{warnings[]}` |
| `email-digest.json` | Latest classified Gmail digest |
| `gc-schedule.json` | Latest GameChanger schedule: `{scraped_at, teams: [{team_id, team_name, child, schedule: [{date, time, type, opponent, location, home_away}]}]}` |
| `sync_meta.json` | Per-source scrape timestamps: `{ixl: {last_run, last_result}, sgy: ..., gc: ...}` |
| `calendar.pdf` | Source PDF (drop in manually each school year) |

## Config

All secrets in `config/env` (gitignored). Template at `.env.example`. `.env` at repo root is legacy — do not use as primary.

`LITELLM_URL` must be the full URL including `/v1` (e.g. `https://llm.grepon.cc/v1`). Both `web/app.py` and `digest.py` append `/v1/chat/completions` to this URL.

Required vars: `LITELLM_URL`, `LITELLM_API_KEY`, `LITELLM_MODEL`, `IXL_EMAIL`, `IXL_PASSWORD`, `SGY_EMAIL`, `SGY_PASSWORD`, `GOG_ACCOUNT`, `NTFY_TOPIC`.

Optional GameChanger vars: `GC_TOKEN` (bearer token from browser session — preferred), or `GC_EMAIL`/`GC_PASSWORD` (Playwright login fallback). `GC_TEAM_MAP` maps team IDs to child names: `"teamid:Ford,teamid2:Jack"`. `SCHOOL_GC_PATH` overrides the default gc-schedule.json path (default: `/app/state/gc-schedule.json`). Run `gc teams --json` to discover team IDs.

Optional on-demand sync: `SYNC_TOKEN` — shared secret for `POST /api/sync`. Generate with: `python3 -c "import secrets; print(secrets.token_hex(32))"`. Unset or shorter than 32 characters (a WARNING is logged, never the value), `/api/sync` returns 501. It is never rendered into any page; each device enters it once in the SPA Settings (see `docs/ops/spa-redesign-rollout.md`).

Optional PowerSchool (4th source): `PS_USERNAME`, `PS_PASSWORD`, `PS_BASE_URL` (or `~/.powersched/.env`, which the scraper reads; the sync checks via `school_dashboard.powerschool.skip_reason`). The `powerschool summary --json` CLI is not yet vendored; with it missing or unconfigured the sync logs an INFO line and skips (no alert). Output file: `PS_FILE` (default `/tmp/powerschool-daily.json`), passed to `school-state update --ps-file`.

Optional `TRUSTED_PROXY_COUNT` (default 0): number of trusted reverse proxies in front of Flask; with N > 0 the sync limiter keys on the Nth `X-Forwarded-For` entry from the right. WARNING: this repo's own `docker-compose.yml` publishes port 5000 on all interfaces, so keep it 0 unless the port is bound to the proxy interface (loopback or the proxy network); otherwise clients can spoof `X-Forwarded-For`. A non-empty invalid value logs one WARNING and counts as 0. Only set it when the port is reachable solely through the proxy (`docs/ops/spa-redesign-rollout.md`).

Optional per-source toggles: `SOURCE_IXL_ENABLED`, `SOURCE_SGY_ENABLED`, `SOURCE_GC_ENABLED`, `SOURCE_POWERSCHOOL_ENABLED` (default enabled; `0/false/no/off`, case-insensitive, disables). A disabled source is skipped by `school-sync.sh` and `/api/sync` with no failure alert and recorded in `sync_meta` as `last_result: "paused"` Cached data from a paused source stays in state and digests. (helpers: `school_dashboard/sources.py::source_enabled`, shell `source_enabled`).

Roster config (`school-dashboard.json`, `children.<Name>`): optional `"ixl": false` marks a child who left IXL; it is never flagged as `IXL unavailable` (quick check, digests, overview, watchdog `scrape_degraded`). Bool only (anything else is ignored with a warning); default true. A roster child with earlier IXL data but no IXL file now is flagged otherwise, with no auto-expiry. See `docs/ops/spa-redesign-rollout.md`.

`SCHOOL_SYNC_META_PATH` overrides the sync metadata path (default: `/app/state/sync_meta.json`). TWA domain verification: `TWA_PACKAGE_NAME` (default: `cc.grepon.school`), `TWA_CERT_FINGERPRINT` (populate after signing APK).

## Tests

**1145 tests run by default** (`pytest --collect-only -q tests/` prints `1145/1148`, 2026-11-08; the 3 `live` tests are deselected). The table below lists only the original files and is NOT exhaustive; the next line is the older 2026-07-07 note (124 tests across 10 files; it
supersedes the older "101 tests / 7 files" figure, which predates several files
below and referenced two files — `test_db.py`, `test_calendar_import.py`,
`test_intel.py` — that no longer exist because `db.py`'s events-table schema and
`calendar_import.py`/`intel.py` themselves were removed). All use mocks/fixtures
built inline in Python — no live credentials needed, and **no test in this repo
reads a recorded real-scraper-output fixture** (see the contract-test gap below).

```
tests/test_api_items.py        6 tests — /api/items GET/POST/PATCH/DELETE
tests/test_digest.py          40 tests — digest build (morning/afternoon/night/weekly), ntfy send, gc event loading + card rendering, build_quick_check
tests/test_html.py             6 tests — dashboard HTML rendering
tests/test_inline_agent.py     6 tests — inline AI agent component backend support
tests/test_items.py            9 tests — db.py items table CRUD
tests/test_llm.py             12 tests — school_dashboard.llm SSE-tolerant chat client
tests/test_readiness.py        8 tests — readiness.py checklist scoring (pure, deterministic, no LLM)
tests/test_sync.py            18 tests — /api/sync auth, concurrency, /api/sync/status, /api/sync/meta, assetlinks, freshness, sync_meta writes
tests/test_sync_meta.py        8 tests — sync_meta module read/write, env var path
tests/test_wrapper_scripts.py 11 tests — run-digest.sh / run-weekly.sh LOGDIR logic, bash syntax
tests/test_sync_orchestration.py 11 tests — runs the real sync/school-sync.sh (+ gc-refresh.sh, gc-scrape.sh, school-state) against fake ixl/sgy/gc/powerschool CLIs on PATH: all ok, one source failing, all failing, gc failing, gc unconfigured, paused source keeps cached data, maintenance at start and mid-sync, fatal state-update failure, missing gc-refresh.sh aborting the sync, in-flight marker. Characterisation tests pin existing behaviour.
```

**Verification gaps an agent should know about before trusting "tests pass" as proof
of correctness** (details and fixes in `.agent_native/agent_roadmap.md`):
- No recorded fixtures of real `sgy`/`ixl`/`gc` `--json` output anywhere in `tests/`
  — every test hand-builds its input dict. No schema/contract check exists between
  what the three vendored scrapers actually emit and what `state.py`/`readiness.py`/
  `digest.py` expect, despite the scrapers living in independently-versioned repos
  under `vendor/`.
- `tests/test_sync_orchestration.py` covers `sync/school-sync.sh` / `gc-scrape.sh` orchestration with fake local CLIs;
  real accounts, real scraper output and the network remain untested and can only be verified by a live run.
- `.github/workflows/docker.yml` never runs tests; `tests.yml` does (pytest, SPA checks, non-blocking e2e).

## Sync, Staleness & Vendor Model

- **Known gaps in `school-sync.sh` (pinned by characterisation tests, not endorsed):** (a) there is no lock file, only
  per-PID `sync.running.$$` markers, so overlapping runs both scrape and merge (state writers are serialised on the
  state lock); (b) a missing `/app/sync/gc-refresh.sh` aborts the whole sync under `set -e` (exit 127), so later
  sources and the merge never run. `SYNC_SCRIPT_DIR` (default `/app/sync`) overrides where the sibling scripts live, for tests.
- **GC token keep-alive:** GC tokens last 1h; a host systemd timer keeps the saved browser session
  fresh, and `sync/gc-refresh.sh` (called by `school-sync.sh`) takes the same `/root/.gc/refresh.lock`.
  Install, verify and failure modes: `docs/ops/gc-keepalive.md`.

- **Scraper invocation:** `sync/school-sync.sh` shells out to the `ixl`, `sgy`, and
  `gc` CLIs (installed from `vendor/*` via `pip install -e`), each writing its own
  JSON file (`$IXL_DIR/*.json`, `$SGY_FILE`, `gc-schedule.json`). `school-state
  update` (in `cli.py`) then merges IXL+Schoology JSON into `school-state.json`
  (`state.py`). Each step is wrapped so a scraper failure is non-fatal to the rest
  of the sync (logged as a `WARN`, `write_sync_meta "<source>" "error"` recorded)
  — except the `school-state update` merge step itself, which is fatal
  (`|| { log "ERROR..."; exit 1; }`) since everything downstream depends on it.
- **Staleness / sync-state tracking (`sync_meta.py`):** a small, deliberately
  simple module — `read_sync_meta()`/`write_sync_source(source, result)` read/write
  `state/sync_meta.json` (`{source: {last_run, last_result}}`) under a threading
  lock, defaulting to `{}` on any read error. There is no judgment call here: it's
  a timestamp ledger, not a heuristic. Where an agent *could* get stuck is
  interpreting **freshness thresholds** — nothing in `sync_meta.py` itself defines
  "how stale is too stale"; that's computed ad hoc in `web/app.py`'s `/api/sync/
  status`/`/api/sync/meta` handlers and in the SPA. If asked to change staleness
  behavior, search `web/app.py` and `web/spa/` for where `last_run` timestamps get
  compared to `datetime.now()`, not `sync_meta.py`.
- **Vendor pinning:** `vendor/{ixl-scrape,schoology-scrape,gc,powerschool-scrape}` are git submodules
  pinned to a specific commit each (see `.gitmodules` for URLs). `git submodule
  update --remote vendor/<name>` moves the pin to that submodule's current default-
  branch HEAD — this is a manual, unreviewed judgment call with **no automated
  check** that the new version's JSON output shape still matches what this repo's
  `state.py`/`readiness.py`/`digest.py` expect (see contract-test gap above). Treat
  any submodule bump as requiring a manual read of that vendor repo's own
  CHANGELOG/diff before merging, until `.agent_native/agent_roadmap.md` item 4 is
  addressed.
- **Schoology auto-archive (`schoology_autoarchive.py`, run by `school-state update` after the sgy merge):**
  pending schoology items carry `last_seen`/`missed`; `missed` only increments on a *trusted* scrape (no
  top-level `warnings`, child entry parses, not suspiciously empty). Archived (removed from state, appended to
  `<state dir>/archive/schoology-auto-YYYY-MM.jsonl`, written first) when `missed >= MISSES_TO_ARCHIVE` (3) and
  absent `>= MIN_DAYS_ABSENT` (3) days; an identical scrape file (sha256) never counts twice. Mass-change guard: >50% (and >=3) or all (and >=2) of a child's pending items at once archives nothing.
  Caveat: the sgy scrape only covers a due-date window, so items with a future `due` are never counted or archived.
  State writers (`update`, `action add/complete`, `schoology reset`) serialise on `<state file>.lock` (60 s timeout).
- **LLM-driven digest generation:** the *data assembly and formatting* in
  `digest.py` (which events/assignments/facts go into the prompt, ntfy delivery,
  digest-carousel card structure) is ordinary deterministic code, covered by
  `tests/test_digest.py`, and safe for an agent to modify/verify without any
  special judgment call. The one genuine human-judgment surface is the **prompt
  text itself** sent to LiteLLM via `school_dashboard/llm.py::chat_completion` —
  changing tone/wording there was a product decision, not something to infer from
  tests (every test mocks `requests.post` and never asserts on exact prompt
  wording). If a bug report is about digest *tone* rather than *content/formatting*,
  flag it for human review of the prompt string rather than iterating blindly.

## Staleness Watchdog (`watchdog.py`)

`school-state watchdog [--dry-run] [--json]` (cron 8:30 + 16:30 daily via `sync/run-watchdog.sh`, maintenance-gated like run-digest.sh) pushes an ntfy alert when a source is overdue. Pure functions `expected_sync`/`check_source`/`plan_alerts` take an injected `now`; `run()` is the I/O shell. `sync_meta` now records `last_ok` (moves only on `ok`; legacy entries fall back to `last_run` when `last_result == "ok"`). Rules: `never_ok` (no success on record), `failing` (last result error and last_ok > 24h), `stale` (last_ok < latest scheduled sync - 90 min, judged only once the run is 90 min past due; schedule const `SYNC_SCHEDULE`, weekdays only, zone from `TZ`). Disabled sources (`SOURCE_*_ENABLED=0`), unconfigured gc/powerschool are skipped; `school-state.json` mtime is checked as pseudo-source `state` — only a backstop, since manual actions also rewrite that file. `never_ok` waits until one scheduled sync + grace has passed since the source's `first_seen`. `state/watchdog.json` = `{alerts: {source: {issue, first_alerted, last_alerted}}, first_seen: {source: iso}}`; one dedupe key per source (failing/stale label flips do not re-alert; re-alert after 24h; single recovered push). Exit 0 healthy/skipped, 1 problems, 2 internal error (failed push via `requests.post` + `raise_for_status`, or unset `NTFY_TOPIC` logged as ERROR; alert state left unwritten so it retries). Silence: `SOURCE_<NAME>_ENABLED=0` or `WATCHDOG_ENABLED=0`. Keep `SYNC_SCHEDULE` in step with `docker/crontab`. Extra rule `scrape_degraded` (`check_scrape_degraded`, pure, injected `now`): `school-state update` records `scrape_meta` in the state (`{ixl|schoology: {unavailable: {child|"*": {reason, since}}}}`, `school_dashboard/scrape_meta.py`; `since` is kept while an entry stays unavailable, dropped on recovery); when the oldest `since` of a source is >= 24h old one problem is raised with dedupe key `ixl_scrape` / `sgy_scrape` (same 24h re-alert and recovery push; skipped with `SOURCE_*_ENABLED=0`, `WATCHDOG_ENABLED=0`, maintenance). The same entries appear as plain-language warnings (with their age) in `/api/overview` `warnings` and `/api/dashboard` `scrape_warnings`; the quick check says `IXL unavailable` / `Schoology unavailable` for those children.

## Credential Handling (`secrets/`)

This repo has a `secrets/` directory (client OAuth secret, `env`, IXL account
credentials, `gog` OAuth credentials) used for local/manual testing outside Docker.
Verified 2026-07-07: `secrets/` is covered by `.gitignore` (line rule `secrets/`)
and has **never** been committed — `git ls-files secrets/` and `git log --all -- 
secrets/` both return nothing. Keep it that way:
- Never `git add -f` anything under `secrets/`.
- Never print, log, or copy the contents of any file under `secrets/` into a PR
  description, commit message, issue, or generated doc.
- The canonical documented secret locations for actual deployment are `config/env`
  (gitignored, primary) and `.env.example` (template, safe to read/edit) — `secrets/`
  itself is not referenced anywhere in the Docker/cron path (`docker/entrypoint.sh`
  loads `config/env`), so treat it as a local-dev-only credential cache, not a file
  the app depends on at runtime.

## Submodule Updates

```bash
git submodule update --remote vendor/ixl-scrape
git submodule update --remote vendor/schoology-scrape
git submodule update --remote vendor/gc
git submodule update --remote vendor/powerschool-scrape
git add vendor/ && git commit -m "chore: update scrapers"
```

## End-to-end tests

- `tests/e2e/test_pipeline_synthetic.py` runs in plain `pytest tests/` (synthetic scraper files -> `school-state update` -> state -> quick check / Flask API / morning-digest prompt, LLM and ntfy mocked).
- `tests/e2e/live/` is opt-in (`@pytest.mark.live`, excluded by `addopts`): `LIVE_E2E=1 pytest -m live tests/e2e/live -q`. `LIVE_E2E=1` means credentials are required: a CLI failure FAILS (only a missing CLI or its explicit "not configured" message skips). Failure output is redacted by `tests/e2e/live/conftest.py` (proven by `tests/test_live_redaction.py`). Optional `LIVE_ROSTER_JSON=path` supplies a roster. Writes only to tmp dirs; messages never print names or titles.
- `scripts/smoke-live.sh` is the post-deploy smoke check: `BASE_URL=http://host:5000 [SYNC_TOKEN=..] [MAX_AGE_HOURS=30] scripts/smoke-live.sh [--container NAME]`. Exit 1 on any FAIL.
