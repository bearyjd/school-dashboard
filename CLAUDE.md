# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

All-in-one Docker deployment for the family school dashboard. Scrapes IXL and Schoology, parses the school calendar PDF, extracts events/facts from Gmail via LiteLLM, sends a morning digest via ntfy, and serves a Flask web app with a chat interface.

**External dependency:** LiteLLM proxy at `LITELLM_URL` (e.g. `http://your-litellm-host:8080`). Not included — point to your own instance.

## Quick Start

```bash
git clone --recurse-submodules <repo>
cp .env.example config/env        # fill in secrets
# drop 2025-2026 calendar PDF into state/calendar.pdf
gog auth add EMAIL                # one-time Google OAuth setup
docker compose up -d
```

## Development (no Docker)

```bash
pip install -e ".[server]" -e vendor/ixl-scrape -e vendor/schoology-scrape -e vendor/gc
playwright install chromium
pytest                            # run all tests
pytest tests/test_db.py -v        # single file
pytest -k "test_name"             # single test
```

## One-off Commands

```bash
# Import school calendar PDF into DB
python -m school_dashboard.calendar_import state/calendar.pdf state/school.db

# Force a morning digest (bypasses 6am hour check)
set -a && source config/env && set +a
python3 -c "
from datetime import date
import json, os, sys
sys.path.insert(0, '.')
from school_dashboard.db import query_upcoming_events, load_facts
from school_dashboard.digest import build_digest_text, send_ntfy
events = query_upcoming_events('state/school.db', from_date=date.today().isoformat(), days=7)
text = build_digest_text(events=events, state={}, facts=[], litellm_url=os.environ['LITELLM_URL'], api_key=os.environ['LITELLM_API_KEY'], model=os.environ['LITELLM_MODEL'])
print(text)
"
```

## Architecture

```
school_dashboard/       Core Python package
  db.py                 SQLite schema (events table + facts.json). INSERT OR IGNORE dedup.
  calendar_import.py    Parse school calendar PDF → events DB. Uses pypdf + line-by-line day detection.
  intel.py              Post classified emails to LiteLLM → extract events/facts.
  digest.py             Build morning digest text via LiteLLM. Send to ntfy.sh push.
  email.py              Gmail digest fetch + classification.
  state.py              Aggregate IXL + Schoology JSON into school-state.json.
  cli.py                school-state CLI entry point.
  html.py               Render school-state.json → dashboard HTML.

web/
  app.py                Flask app. Routes: /api/chat, /api/items*, /api/dashboard, /api/readiness, /api/digest/* (carousel)
  templates/index.html  Dashboard iframe + chat tab. Uses marked.js for markdown rendering.

sync/
  school-sync.sh        Cron script: IXL → SGY → GC → state → email-sync → intel → HTML → digest (6am only).
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

- **Calendar import (one-time):** `calendar_import.py` → parses PDF pages, detects spaced month headers (A U G U S T), classifies 10 event types (NO_SCHOOL, EARLY_RELEASE, MASS…), inserts into `events` table via INSERT OR IGNORE.
- **Email intel (each sync):** `school-state email-sync` fetches Gmail digest → `intel.py` calls LiteLLM with each SCHOOL/CHILD_ACTIVITY email → extracts dated events + recurring facts → inserts into DB.
- **GameChanger scrape (each sync, non-fatal):** `gc-scrape.sh` calls `gc teams --json`, iterates team IDs, calls `gc summary --json --team ID` per team, writes merged `gc-schedule.json`. Skipped if `GC_TOKEN` and `GC_EMAIL` are both unset.
- **Morning/afternoon/night digest (6am/2:30pm/8:30pm):** `digest.py` queries events + IXL state + facts + gc-schedule.json → LiteLLM synthesizes → creates `digests` carousel → ntfy.sh push with deep-link.
- **Chat:** `/api/chat` builds system prompt with 30-day events + facts + full state JSON → streams reply from LiteLLM.

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

## State Files (all gitignored, in state/)

| File | Contents |
|------|----------|
| `school.db` | SQLite: `events` table (calendar + email-extracted events), `items` table (manual + scraped tasks), `digests` table (carousel history) |
| `facts.json` | Array of `{subject, fact, source, created_at}` — long-term memory |
| `school-state.json` | Latest IXL + Schoology aggregate |
| `email-digest.json` | Latest classified Gmail digest |
| `gc-schedule.json` | Latest GameChanger schedule: `{scraped_at, teams: [{team_id, team_name, child, schedule: [{date, time, type, opponent, location, home_away}]}]}` |
| `calendar.pdf` | Source PDF (drop in manually each school year) |

## Config

All secrets in `config/env` (gitignored). Template at `.env.example`. `.env` at repo root is legacy — do not use as primary.

`LITELLM_URL` must be the bare base URL (e.g. `http://your-litellm-host:8080`) — never include `/v1`. Both `web/app.py` and `digest.py` append `/v1/chat/completions` themselves.

Required vars: `LITELLM_URL`, `LITELLM_API_KEY`, `LITELLM_MODEL`, `IXL_EMAIL`, `IXL_PASSWORD`, `SGY_EMAIL`, `SGY_PASSWORD`, `GOG_ACCOUNT`, `NTFY_TOPIC`.

Optional GameChanger vars: `GC_TOKEN` (bearer token from browser session — preferred), or `GC_EMAIL`/`GC_PASSWORD` (Playwright login fallback). `GC_TEAM_MAP` maps team IDs to child names: `"teamid:Ford,teamid2:Jack"`. `SCHOOL_GC_PATH` overrides the default gc-schedule.json path (default: `/app/state/gc-schedule.json`). Run `gc teams --json` to discover team IDs.

## Tests

33 tests across 4 files. All use mocks — no live credentials needed.

```
tests/test_db.py              7 tests  — SQLite schema, dedup, facts
tests/test_calendar_import.py 12 tests — PDF parsing, event classification
tests/test_intel.py           4 tests  — LiteLLM extraction, error handling
tests/test_digest.py          10 tests — digest build, ntfy send, gc event loading + card rendering
```

## Submodule Updates

```bash
git submodule update --remote vendor/ixl-scrape
git submodule update --remote vendor/schoology-scrape
git submodule update --remote vendor/gc
git add vendor/ && git commit -m "chore: update scrapers"
```
