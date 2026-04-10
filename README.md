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
  app.py                Flask app. /api/chat streams reply from LiteLLM.
  templates/index.html  Dashboard iframe + chat tab (marked.js markdown rendering).

sync/
  school-sync.sh        Cron script: IXL → SGY → state → email-sync → intel → HTML → digest.

vendor/
  ixl-scrape/           git submodule (pip install -e)
  schoology-scrape/     git submodule (pip install -e)

docker/
  Dockerfile            python:3.12-slim + gog v0.12.0 + Playwright/Chromium + scrapers
  entrypoint.sh         loads config/env → starts cron → starts Flask on :5000
  crontab               6:00am + 2:30pm weekday syncs
```

## Data Flow

| Time | What happens |
|------|-------------|
| **6:00am** (weekdays) | IXL scrape → SGY scrape → state merge → email intel → dashboard HTML → ntfy.sh digest |
| **2:30pm** (weekdays) | IXL + SGY + email re-scrape (catch late homework posts) |
| **On demand** | `/api/chat` — query 30-day events + facts + full state via LiteLLM |

## Configuration

Copy `.env.example` to `config/env` and fill in all values:

| Variable | Required | Description |
|----------|----------|-------------|
| `LITELLM_URL` | Yes | LiteLLM proxy base URL (e.g. `http://192.168.1.20:4000`) |
| `LITELLM_API_KEY` | Yes | API key for LiteLLM proxy |
| `LITELLM_MODEL` | Yes | Model name (e.g. `claude-sonnet`) |
| `IXL_EMAIL` | Yes | IXL student login email |
| `IXL_PASSWORD` | Yes | IXL student login password |
| `SGY_EMAIL` | Yes | Schoology parent login email |
| `SGY_PASSWORD` | Yes | Schoology parent login password |
| `SGY_BASE_URL` | Yes | Schoology API base URL |
| `SGY_SCHOOL_NID` | Yes | Schoology school network ID |
| `GOG_ACCOUNT` | Yes | Gmail account for gog OAuth |
| `NTFY_TOPIC` | Yes | ntfy.sh topic slug for push notifications |
| `SCHOOL_EMAIL_ACCOUNT` | Yes | Gmail account for school email intel |
| `BRYN_EMAIL` | No | Additional digest recipient email |

Path overrides (default to `/app/state/` inside Docker):

```
SCHOOL_STATE_PATH, SCHOOL_DB_PATH, SCHOOL_FACTS_PATH,
SCHOOL_EMAIL_DIGEST, SCHOOL_CALENDAR_PDF
```

## State Files

All gitignored, stored in `state/`:

| File | Contents |
|------|----------|
| `school.db` | SQLite: `events` table (calendar + email-extracted events) |
| `facts.json` | Long-term memory: `{subject, fact, source, created_at}` |
| `school-state.json` | Latest IXL + Schoology aggregate |
| `email-digest.json` | Latest classified Gmail digest |
| `calendar.pdf` | Source PDF (drop in manually each school year) |

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

## Development (no Docker)

```bash
pip install -e ".[server]" -e vendor/ixl-scrape -e vendor/schoology-scrape
playwright install chromium
pytest                            # run all tests
pytest tests/test_db.py -v        # single file
pytest -k "test_name"             # single test
```

## Tests

26 tests across 4 files. All use mocks — no live credentials needed.

```
tests/test_db.py              7 tests  — SQLite schema, dedup, facts
tests/test_calendar_import.py 12 tests — PDF parsing, event classification
tests/test_intel.py           4 tests  — LiteLLM extraction, error handling
tests/test_digest.py          3 tests  — digest build, ntfy send
```

## Submodule Updates

```bash
git submodule update --remote vendor/ixl-scrape
git submodule update --remote vendor/schoology-scrape
git add vendor/ && git commit -m "chore: update scrapers"
```

## License

Private family tool. Not licensed for redistribution.
