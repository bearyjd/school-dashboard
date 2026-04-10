# Email + Calendar + Memory Intelligence Layer — Design Spec

**Date:** 2026-04-09
**Project:** school-dashboard
**Status:** Approved

---

## Overview

Add a persistent, future-oriented intelligence layer to the school dashboard. The system parses the school school calendar PDF, scans Gmail for school-related emails, and accumulates structured events and learned facts into a local SQLite database and JSON facts store. A morning digest synthesized by LiteLLM is pushed via ntfy to both parents and optionally emailed as backup. The chat interface gains full access to upcoming events and accumulated facts.

---

## Architecture

### Data Stores

**`/opt/school/state/school.db`** — SQLite

```sql
CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,        -- ISO date: 2025-10-13
    title      TEXT NOT NULL,
    type       TEXT NOT NULL,        -- see Event Types below
    child      TEXT,                 -- ford / jack / penn / NULL (school-wide)
    source     TEXT NOT NULL,        -- calendar_pdf / email / schoology
    notes      TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_events_dedup ON events(date, title);
```

Event types: `NO_SCHOOL`, `EARLY_RELEASE`, `MASS`, `ASSEMBLY`, `PARENT_MTG`, `RETREAT`, `TESTING`, `SPORTS`, `FIELD_TRIP`, `CONCERT`, `OTHER`

**`/opt/school/state/facts.json`** — learned facts array

```json
[
  {
    "subject": "jack",
    "fact": "soccer practice Tuesdays after school",
    "source": "email",
    "learned": "2025-09-03"
  },
  {
    "subject": "general",
    "fact": "coach Thompson emails from coachT@smcs.org",
    "source": "email",
    "learned": "2025-09-03"
  }
]
```

Deduplication: new facts are only appended if no existing entry matches on `subject` + `fact` (case-insensitive).

---

## Components

### 1. `calendar-import.py` — One-time PDF parser

- Reads `/opt/school/state/calendar.pdf` (copy of the school planning calendar)
- Uses pypdf to extract text page by page
- Parses month/year headers and day-by-day event text
- Classifies each event into the appropriate type
- Bulk-inserts into `school.db`, source = `calendar_pdf`
- Run once manually; safe to re-run (UNIQUE index prevents duplicates)

**Input:** `/opt/school/state/calendar.pdf`
**Output:** ~60 rows in `events` table

### 2. `email-intel.py` — Email event + fact extractor

Runs as part of `school-sync.sh` after the existing `email.py` classification step.

Flow:
1. Call `email.py` to fetch + classify new emails (already implemented)
2. For each email in SCHOOL or CHILD_ACTIVITY buckets, POST to LiteLLM:

```
System: You extract structured information from school emails for a family with three kids at school: the children.

Extract from this email:
- Calendar events: specific dates mentioned with what's happening
- Recurring facts: schedules, contacts, patterns worth remembering

Return JSON only:
{
  "events": [{"date": "YYYY-MM-DD", "title": "...", "type": "...", "child": "ford|jack|penn|null", "notes": "..."}],
  "facts": [{"subject": "ford|jack|penn|general", "fact": "..."}]
}
If nothing extractable, return {"events": [], "facts": []}.
```

3. Merge results into `school.db` and `facts.json` with deduplication

### 3. Morning digest (updated `school-sync.sh` step 6)

Runs at 6am only. Pulls:
- Events in next 7 days from `school.db` (ordered by date)
- Assignments due this week from `school-state.json` (Schoology)
- IXL status from `school-state.json`
- New email items since yesterday

Posts to LiteLLM using `cron-prompts/morning-briefing.md` as the system prompt. The prompt is editable without code changes.

Output: 5–10 line natural language summary, sent to:
- ntfy topic `your-ntfy-topic` (both parents receive)
- Email: `parent@example.com` + the other parent's address (configurable in `/opt/school/config/env`)

### 4. Chat context expansion (`/opt/school/web/app.py`)

The `/api/chat` endpoint currently loads `school-state.json` and email digest. Expand to also include:
- Next 30 days of events from `school.db` (as a formatted list)
- All entries from `facts.json`

This makes questions like "what does Child2 have this week?" and "when is the next no-school day?" answerable directly from structured data rather than from LLM hallucination.

---

## Processing Pipeline

```
school-sync.sh  (6am + 2:30pm weekdays)
  │
  ├── 1. IXL scrape          (existing)
  ├── 2. Schoology scrape    (existing)
  ├── 3. email.py            (wire up — currently never run)
  ├── 4. email-intel.py      (NEW: extract events + facts from classified emails)
  ├── 5. merge state         (existing)
  ├── 6. render HTML         (existing)
  └── 7. morning digest      (6am only, updated to use school.db)
```

---

## Configuration

New entries in `/opt/school/config/env`:

```bash
SCHOOL_DB_PATH=/opt/school/state/school.db
SCHOOL_FACTS_PATH=/opt/school/state/facts.json
SCHOOL_CALENDAR_PDF=/opt/school/state/calendar.pdf
DIGEST_EMAIL=parent@example.com          # the other parent's email for digest backup
DIGEST_EMAIL_FROM=parent@example.com    # sender via gog
```

---

## Decisions & Positions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Memory store | SQLite + facts.json | Date queries need structure; facts are amorphous |
| Calendar source | PDF parse (one-time) | Already have 2025-2026 PDF; ICS not available |
| Email extraction | LiteLLM per email | Existing email.py already classifies; LLM extracts structure |
| Fact deduplication | subject+fact string match | Simple, avoids LLM-generated near-duplicates |
| Digest recipients | ntfy (both) + email backup | Parent2 receive-only; email as fallback |
| Prompt management | `cron-prompts/morning-briefing.md` | Editable without code changes |

---

## Out of Scope

- Per-sport or per-activity schedule tracking (handled via email fact extraction)
- Parent2 having chat interface access (receive-only for now)
- Historical digest archiving (future enhancement)
- ICS calendar ingestion (PDF covers the full school year)
- Real-time alerts (digest is once/twice daily only)

---

## Open Questions

- the other parent's email address (needed before first digest run — add to `/opt/school/config/env`)
- Whether `email.py` needs credential fixes before first run (GOG_KEYRING_PASSWORD is empty — needs verification)
