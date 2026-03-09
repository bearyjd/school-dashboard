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
school-state action list --child <name>
school-state action add <name> "Permission slip for field trip" --due 2026-03-15 --source email
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
ssh root@<server> 'bash -s' < install-lxc.sh
```

Or manually:
```bash
pip install git+https://github.com/bearyjd/school-dashboard --break-system-packages
git clone https://github.com/bearyjd/school-dashboard.git /opt/school-dashboard
```

## Configuration

Children and name aliases are stored in `/etc/school-dashboard/config.json` (created by `install-lxc.sh`). Override path with `SCHOOL_DASHBOARD_CONFIG` env var.

```json
{
  "children": {
    "Alice": {"grade": "2nd", "school": "Example School"},
    "Bob": {"grade": "5th", "school": "Example School"}
  },
  "name_aliases": {
    "ali": "Alice",
    "alice": "Alice",
    "bob": "Bob"
  }
}
```

IXL uses short account names from `accounts.env`, Schoology uses full first names. The `name_aliases` map ensures both resolve to the same canonical name.
