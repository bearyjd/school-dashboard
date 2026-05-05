<!-- Generated: 2026-05-05 | Files scanned: 48 | Token estimate: ~700 -->

# Data

## SQLite: `state/school.db`

Tables created in `school_dashboard/db.py` via `init_db()`.

### `items`

Manual + scraped tasks. Backs `/api/items`.

```sql
CREATE TABLE items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    child        TEXT NOT NULL,
    title        TEXT NOT NULL,
    due_date     TEXT,
    type         TEXT NOT NULL DEFAULT 'assignment',
    source       TEXT NOT NULL DEFAULT 'manual',
    completed    INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    notes        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### `digests`

Carousel history for digest deep-links.

```sql
CREATE TABLE digests (
    id         TEXT PRIMARY KEY,    -- ULID
    created_at TEXT NOT NULL,
    title      TEXT NOT NULL,       -- e.g. "Morning briefing — Mon May 5"
    cards      TEXT NOT NULL        -- JSON array of card objects
);
```

`cards[i].done` toggled via `PATCH /api/digest/<id>/cards/<index>`.

## JSON state files (`state/`)

All paths are env-overridable. Defaults shown.

### `school-state.json` (`SCHOOL_STATE_PATH`)

```
{
  "children": [
    {
      "name": str,
      "ixl": { diagnostics, skills[], trouble_spots[] },
      "sgy": { assignments[], grades{}, announcements[] }
    }
  ],
  "last_updated": ISO datetime
}
```

### `email-digest.json` (`SCHOOL_EMAIL_DIGEST`)

```
[
  {
    "id": str,
    "subject": str,
    "from": str,
    "date": str,
    "body": str,
    "bucket": "SCHOOL" | "CHILD_ACTIVITY" | "FINANCIAL" | "SKIP",
    "attachments": [ {filename, content} ]
  }
]
```

### `gc-schedule.json` (`SCHOOL_GC_PATH`)

Written by `sync/gc-scrape.sh`.

```
{
  "scraped_at": ISO datetime,
  "teams": [
    {
      "team_id": UUID,
      "team_name": str,
      "child": str,                 -- from GC_TEAM_MAP or team name
      "schedule": [
        {date, time, type, opponent, location, home_away, ...}
      ]
    }
  ]
}
```

Layer 2 in gc-scrape.sh refuses to overwrite this file when `sum(len(team.schedule) for team in teams) == 0` — that signature means every `gc summary` call 401'd silently.

### `sync_meta.json` (`SCHOOL_SYNC_META_PATH`)

```
{
  "ixl": {"last_run": ISO, "last_result": "ok"|"error"},
  "sgy": {"last_run": ISO, "last_result": "ok"|"error"},
  "gc":  {"last_run": ISO, "last_result": "ok"|"error"}
}
```

Read/written atomically by `school_dashboard/sync_meta.py`. Surfaces at `/api/sync/meta` for the SPA freshness display.

### `facts.json` (`SCHOOL_FACTS_PATH`)

```
[
  {"subject": str, "fact": str, "source": str, "created_at": ISO}
]
```

> _Aspirational._ Populated by `intel.py` when present; the file path is honored by `digest.py` and `web/app.py` for chat context regardless.

## Caches

| File | Owner | TTL |
|---|---|---|
| `~/.gc/sessions/playwright_context.json` | `vendor/gc` | refreshed each Playwright run |
| `~/.gc/.env` `GC_TOKEN` / `GC_DEVICE_ID` | `vendor/gc` `_update_env_token` | rewritten on every successful auth |
| in-process Google Calendar cache | `gcal.py` | per-process, request-life |
