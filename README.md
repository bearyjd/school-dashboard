# school-dashboard

Persistent state manager + static dashboard for family school situational awareness. Aggregates data from IXL, Schoology, and email into a single JSON state file, regenerates a mobile-friendly HTML dashboard, and provides CLI tools for managing action items.

## Setup

```bash
pip install -e .
```

Requires `ixl` and `sgy` CLIs to be installed separately for data scraping.

## How It Works

```
6am cron (no LLM)              OpenClaw agents (with LLM)
┌─────────────────┐            ┌──────────────────────┐
│  school-sync.sh │            │  morning-briefing     │
│  ├─ ixl-cron.sh │            │  reads state file     │
│  ├─ sgy summary │──state──>  │  scans email          │
│  ├─ state update│  file      │  updates action items │
│  └─ html regen  │            │  sends Signal digest  │
└─────────────────┘            └──────────────────────┘
```

The 6am sync scrapes IXL + Schoology and merges everything into `/var/lib/openclaw/school-state.json`. OpenClaw agents read the compact state file (~5KB) instead of running scrapers themselves, avoiding context window blowup.

## CLI

```bash
# Merge latest scraper output into state
school-state update

# Regenerate static HTML dashboard
school-state html

# Print current state
school-state show
school-state show --json

# Action items
school-state action list
school-state action list --child Ford
school-state action add Ford "Permission slip for field trip" --due 2026-03-15 --source email
school-state action complete abc123def456
```

## State File

Default location: `/var/lib/openclaw/school-state.json`

Override with `--state-file` flag or `SCHOOL_STATE_PATH` env var.

## Cron Setup

### 1. System cron (data refresh, no LLM)

```bash
0 6 * * * /opt/school-dashboard/school-sync.sh 2>/tmp/school-sync.log
```

### 2. OpenClaw crons (use rewritten prompts from `cron-prompts/`)

| Time | Job | Model | What it does |
|------|-----|-------|-------------|
| 7am | Morning Briefing | Sonnet | Reads state, scans email, calendar sync, Signal digest |
| 2pm | Afternoon Update | Haiku | Reads state, formats IXL + homework report |
| 6pm | Evening Email | Haiku | Light inbox scan, updates action items |

See `cron-prompts/` for the exact prompt text for each job.

## Dashboard

Static HTML generated at `/var/lib/openclaw/school-dashboard.html`. Mobile-friendly dark theme. Includes:

- Action items (pending, sorted by due date)
- IXL progress per child with progress bars
- Grades per course (flags below B)
- Upcoming assignments

The HTML embeds the full state JSON in a hidden `<script>` tag for future Flask upgrade — just serve the JSON at `/api/state` and swap the template to Jinja server-side.

## Install on Server

```bash
pip install -e /opt/school-dashboard
cp school-sync.sh /opt/school-dashboard/
chmod +x /opt/school-dashboard/school-sync.sh
mkdir -p /var/lib/openclaw
```

## Child Name Mapping

IXL uses short account names (`ford`, `jack`, `penn`), Schoology uses full names (`Ford Beary`). The `NAME_ALIASES` dict in `state.py` maps all variants to canonical names. Edit if your account names differ.
