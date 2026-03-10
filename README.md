# school-dashboard

Persistent state manager + static dashboard for family school situational awareness. Aggregates data from IXL, Schoology, and email into a single JSON state file, regenerates a mobile-friendly HTML dashboard, and provides CLI tools for managing action items.

## Setup

```bash
pip install -e .
```

Requires `ixl` and `sgy` CLIs to be installed separately for data scraping.

## How It Works

```
6:00 AM  school-sync.sh (system cron, NO LLM)
         ├── IXL scrape → state
         ├── SGY scrape → state
         ├── Email sync → fetch all emails, strip HTML,
         │                extract PDF text, classify,
         │                write email-digest.json
         └── Regen dashboard HTML

6:05 AM  Email Triage (Haiku, 60s, Signal)
         └── Reads digest, creates action items, flags uncertain emails

7:00 AM  Morning Briefing (Sonnet, 180s)
         ├── school-state show     (~1.3KB)
         ├── school-state email-show (~1.5KB)
         ├── Calendar events
         ├── Full body fetch only if snippet unclear (max 3)
         └── Signal digest
         TOTAL CONTEXT: ~6KB base, ~21KB worst case

2:30 PM  school-sync.sh (system cron, Mon-Fri, NO LLM)
         └── Re-scrape IXL + SGY + email (catch late homework posts)

2:35 PM  Afternoon Email Triage (Haiku, 60s, Signal, Mon-Fri)
         └── Reads digest, creates action items, flags uncertain emails

3:00 PM  Afternoon Update (Haiku, 60s)
         └── school-state show → IXL + homework + grades → Signal

6:00 PM  Evening Email (Haiku, 120s)
         └── Light inbox scan → update action items → Signal
```

Context budget dropped from ~150KB/day to ~28KB worst case by pre-processing emails and reading compact state files instead of raw scraper output.

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

# Email pre-processing
school-state email-sync --account user@example.com
school-state email-show
school-state email-show --json
```

## Email Pipeline

`school-state email-sync` fetches all inbox emails, strips HTML to plain text snippets, downloads and extracts text from PDF attachments, and classifies each email:

| Bucket | Rule |
|---|---|
| `SCHOOL` | Sender domain matches configured school domains |
| `CHILD_ACTIVITY` | Subject contains activity keywords (practice, game, permission, etc.) |
| `STARRED` | User-starred in Gmail |
| `FINANCIAL` | Subject contains financial keywords |
| `SKIP` | Promotions, social, GitHub, known marketing senders |
| `UNKNOWN` | Everything else — included in digest for LLM triage |

Processed emails are labeled `OpenClaw/Scanned` in Gmail to prevent re-scanning.

## State File

Default location: `/var/lib/openclaw/school-state.json`

Override with `--state-file` flag or `SCHOOL_STATE_PATH` env var.

## Dashboard

Static HTML at `/var/lib/openclaw/school-dashboard.html`. Mobile-friendly dark theme. Shows action items, IXL progress bars, grades (flags below B), and upcoming assignments.

The HTML embeds state JSON in a hidden `<script>` tag for future Flask upgrade.

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

### Children (`/etc/school-dashboard/config.json`)

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

Override path with `SCHOOL_DASHBOARD_CONFIG` env var.

### Email (`/etc/school-dashboard/env`)

```
SCHOOL_EMAIL_ACCOUNT=user@example.com
SCHOOL_DOMAINS=school.org,schoology.com,ccsend.com
```

## .gitignore

```
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
*.egg
.env
```
