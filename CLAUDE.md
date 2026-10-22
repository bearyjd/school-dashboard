# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

All-in-one Docker deployment for the family school dashboard. Scrapes IXL,
Schoology, and GameChanger; aggregates into `school-state.json`; sends
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
pip install -e ".[server]" -e vendor/ixl-scrape -e vendor/schoology-scrape -e vendor/gc
playwright install chromium
pytest tests/                     # run all tests — SCOPE TO tests/, see warning below
pytest tests/test_readiness.py -v # single file
pytest -k "test_name"             # single test
# Run SPA dev server (proxies API to Flask on :5000)
npm --prefix web/spa run dev   # → http://localhost:5173/app/
npm --prefix web/spa run build # tsc -b && vite build — the only automated SPA check today (no unit tests in web/spa yet)
```

**⚠️ Always run `pytest tests/`, never bare `pytest`, from the repo root.**
Verified 2026-07-07: bare `pytest` fails collection with
`ImportPathMismatchError` / `ModuleNotFoundError: No module named 'tests.test_summary'`
because `vendor/ixl-scrape`, `vendor/schoology-scrape`, and `vendor/gc` are each
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

There is no test-running CI workflow — `.github/workflows/docker.yml` only builds
and pushes the Docker image on push to `main`. It does **not** run `pytest`. Treat
"CI is green" as meaning "the Docker image built," not "tests pass," until a test
workflow is added (`.agent_native/agent_roadmap.md` item 2).

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
  state.py              Aggregate IXL + Schoology JSON into school-state.json.
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
  spa/                  Vite + React + TypeScript SPA (`web/spa/`). `npm run build` (tsc -b && vite build) is the only automated check today — no vitest/jest tests exist yet in `package.json` devDependencies.
  templates/index.html  Legacy dashboard iframe + chat tab (pre-SPA). Uses marked.js for markdown rendering.

sync/
  school-sync.sh        Cron script: IXL → SGY → GC → state → email-sync → HTML → digest (email-intel step permanently skipped — see note above). Runs at 6:00am and 2:30pm weekdays.
  gc-scrape.sh          Scrapes GameChanger for all configured teams; writes gc-schedule.json.

vendor/
  ixl-scrape/           git submodule (pip install -e)
  schoology-scrape/     git submodule (pip install -e)
  gc/                   git submodule (pip install -e) — GameChanger CLI

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
| `/api/sync` | POST | Trigger on-demand sync (header: `X-Sync-Token`; JSON: `sources` e.g. `"ixl,sgy"`, `digest` e.g. `"quick"/"full"/"none"`) |
| `/api/sync/status` | GET | Poll sync state (`{running, last_run, last_result, last_sources, last_error}`) |
| `/api/sync/meta` | GET | Per-source sync freshness (`{ixl: {last_run, last_result}, ...}`) |
| `/.well-known/assetlinks.json` | GET | TWA domain verification (fingerprint from `TWA_CERT_FINGERPRINT` env var) |

## State Files (all gitignored, in state/)

| File | Contents |
|------|----------|
| `school.db` | SQLite: `events` table (calendar + email-extracted events), `items` table (manual + scraped tasks), `digests` table (carousel history) |
| `facts.json` | Array of `{subject, fact, source, created_at}` — long-term memory |
| `school-state.json` | Latest IXL + Schoology aggregate |
| `email-digest.json` | Latest classified Gmail digest |
| `gc-schedule.json` | Latest GameChanger schedule: `{scraped_at, teams: [{team_id, team_name, child, schedule: [{date, time, type, opponent, location, home_away}]}]}` |
| `sync_meta.json` | Per-source scrape timestamps: `{ixl: {last_run, last_result}, sgy: ..., gc: ...}` |
| `calendar.pdf` | Source PDF (drop in manually each school year) |

## Config

All secrets in `config/env` (gitignored). Template at `.env.example`. `.env` at repo root is legacy — do not use as primary.

`LITELLM_URL` must be the full URL including `/v1` (e.g. `https://llm.grepon.cc/v1`). Both `web/app.py` and `digest.py` append `/v1/chat/completions` to this URL.

Required vars: `LITELLM_URL`, `LITELLM_API_KEY`, `LITELLM_MODEL`, `IXL_EMAIL`, `IXL_PASSWORD`, `SGY_EMAIL`, `SGY_PASSWORD`, `GOG_ACCOUNT`, `NTFY_TOPIC`.

Optional GameChanger vars: `GC_TOKEN` (bearer token from browser session — preferred), or `GC_EMAIL`/`GC_PASSWORD` (Playwright login fallback). `GC_TEAM_MAP` maps team IDs to child names: `"teamid:Ford,teamid2:Jack"`. `SCHOOL_GC_PATH` overrides the default gc-schedule.json path (default: `/app/state/gc-schedule.json`). Run `gc teams --json` to discover team IDs.

Optional on-demand sync: `SYNC_TOKEN` — shared secret for `POST /api/sync`. Generate with: `python3 -c "import secrets; print(secrets.token_hex(16))"`. Without it, `/api/sync` returns 501.

`SCHOOL_SYNC_META_PATH` overrides the sync metadata path (default: `/app/state/sync_meta.json`). TWA domain verification: `TWA_PACKAGE_NAME` (default: `cc.grepon.school`), `TWA_CERT_FINGERPRINT` (populate after signing APK).

## Tests

**124 tests across 10 files** (recounted 2026-07-07 via `pytest --collect-only`;
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
tests/test_wrapper_scripts.py 11 tests — run-digest.sh / run-weekly.sh LOGDIR logic, bash syntax (does NOT run the full sync scripts — see below)
```

**Verification gaps an agent should know about before trusting "tests pass" as proof
of correctness** (details and fixes in `.agent_native/agent_roadmap.md`):
- No recorded fixtures of real `sgy`/`ixl`/`gc` `--json` output anywhere in `tests/`
  — every test hand-builds its input dict. No schema/contract check exists between
  what the three vendored scrapers actually emit and what `state.py`/`readiness.py`/
  `digest.py` expect, despite the scrapers living in independently-versioned repos
  under `vendor/`.
- `tests/test_wrapper_scripts.py` only exercises a `LOGDIR`-derivation bash snippet
  in isolation, by its own docstring's admission — the real `sync/school-sync.sh`
  and `sync/gc-scrape.sh` orchestration is untested and can only be verified by a
  live run against real accounts.
- `.github/workflows/docker.yml` is the only CI workflow and it never runs `pytest`.

## Sync, Staleness & Vendor Model

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
- **Vendor pinning:** `vendor/{ixl-scrape,schoology-scrape,gc}` are git submodules
  pinned to a specific commit each (see `.gitmodules` for URLs). `git submodule
  update --remote vendor/<name>` moves the pin to that submodule's current default-
  branch HEAD — this is a manual, unreviewed judgment call with **no automated
  check** that the new version's JSON output shape still matches what this repo's
  `state.py`/`readiness.py`/`digest.py` expect (see contract-test gap above). Treat
  any submodule bump as requiring a manual read of that vendor repo's own
  CHANGELOG/diff before merging, until `.agent_native/agent_roadmap.md` item 4 is
  addressed.
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
git add vendor/ && git commit -m "chore: update scrapers"
```
