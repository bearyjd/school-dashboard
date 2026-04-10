<!-- Generated: 2026-04-10 | Files scanned: 34 | Token estimate: ~400 -->

# Data

## SQLite: `state/school.db`

### `events` table
```sql
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,       -- ISO 8601 YYYY-MM-DD
    title       TEXT NOT NULL,
    event_type  TEXT,                -- NO_SCHOOL, EARLY_RELEASE, MASS, etc.
    source      TEXT,                -- 'calendar' | 'email'
    created_at  TEXT DEFAULT (datetime('now'))
    -- UNIQUE constraint: (date, title) for INSERT OR IGNORE dedup
);
```

### Key queries
```python
query_upcoming_events(db_path, from_date, days=7)   # digest
query_upcoming_events(db_path, from_date, days=30)  # chat context
```

## JSON State Files (`state/`)

### `school-state.json`
```
{
  "children": [
    {
      "name": str,
      "ixl": { diagnostics, skills[], trouble_spots[] },
      "sgy": { assignments[], grades{}, announcements[] }
    }
  ],
  "action_items": [ {title, due, completed, created_at} ],
  "last_updated": ISO datetime
}
```

### `facts.json`
```
[
  {
    "subject": str,   -- child name or "family"
    "fact": str,      -- extracted long-term fact
    "source": str,    -- email subject or "manual"
    "created_at": ISO datetime
  }
]
```

### `email-digest.json`
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

## Event Types (calendar_import.py)

| Type | Description |
|------|-------------|
| NO_SCHOOL | School closed |
| EARLY_RELEASE | Early dismissal |
| MASS | School Mass |
| FIELD_TRIP | Field trip |
| REPORT_CARD | Report card day |
| CONFERENCE | Parent-teacher conference |
| HOLIDAY | Holiday |
| SPORT | Sports event |
| ACTIVITY | School activity |
| OTHER | Uncategorized |
