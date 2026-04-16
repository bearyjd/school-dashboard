# Deep Links & Calendar Integration Spec

## Overview

Make every item on the school dashboard clickable — linking directly to the source system where the user can take action. Additionally, pull upcoming events from Google Calendar (which two-way syncs with the family Skylight display) and show them on the dashboard with deep links back to GCal.

## Key Themes

### 1. Deep Links Across All Data Sources

Every item on the dashboard should link to its source:

| Source | Link Target | Data Available |
|--------|-------------|----------------|
| Email action items | Gmail thread | `id` field = Gmail message ID → `https://mail.google.com/mail/u/0/#all/{id}` |
| Schoology assignments | Assignment page | `link` field = relative path → `https://arlingtondiocese.schoology.com{link}` |
| IXL remaining work | IXL login | No per-skill deep links → `https://www.ixl.com/` |
| Calendar events (DB) | Google Calendar event | Need to store `htmlLink` from GCal API when ingesting events |
| Calendar events (GCal API) | Google Calendar event | `htmlLink` field from API response |

### 2. Google Calendar Integration

- **Auth:** Reuse existing `gog` OAuth setup in the Docker container (already handles Gmail)
- **Scope:** Show all events from primary calendar (school, kid activities, family, household)
- **Sync with Skylight:** Two-way sync means GCal is the source of truth for the family calendar display
- **API endpoint:** New `/api/calendar` endpoint returning upcoming events with `htmlLink` deep links
- **Dashboard display:** New "Calendar" mode tab alongside Schoology, IXL, and Email Items

### 3. Schoology Deep Links

The sgy scraper already captures a `link` field per assignment (e.g., `/course/123/assignment/456`). Full URL constructed as:
```
https://arlingtondiocese.schoology.com{assignment.link}
```

Base URL stored as env var `SGY_BASE_URL` (already in `~/.sgy/.env`).

### 4. IXL Links

No per-skill deep links available. Link to generic `https://www.ixl.com/` so kids can log in and start practicing.

## Decisions & Positions

1. **All GCal events, not filtered** — the dashboard serves the whole family, not just school. Trash pickup, baseball practice, and First Communion rehearsal are all relevant.
2. **gog for GCal auth** — already handling Gmail OAuth, just needs Calendar scope added.
3. **Schoology base URL from env** — `SGY_BASE_URL` already exists in the scraper config.
4. **IXL gets a generic link** — no deep link IDs available from the scraper.
5. **Email Gmail links already implemented** — Actions tab has "Open in Gmail ↗" working.

## Open Questions

- Does `gog` currently have Calendar API scope, or does it need to be re-authorized with `https://www.googleapis.com/auth/calendar.readonly`?
- Should GCal events replace the DB events table entirely, or supplement it? (DB has school PDF calendar events that may not be in GCal.)
- Rate limiting / caching for GCal API calls — how often to refresh?

## Constraints & Boundaries

- This is NOT about creating events or writing back to GCal from the dashboard
- This is NOT about modifying the sgy/ixl scrapers (they already have what we need)
- GCal API calls happen server-side only — no client-side OAuth
- The dashboard is LAN-only (192.168.1.14) — no public-facing auth concerns
