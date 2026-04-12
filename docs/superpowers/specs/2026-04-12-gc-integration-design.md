# Design: GameChanger Integration

**Date:** 2026-04-12  
**Status:** Approved  
**Scope:** Add GameChanger extracurricular schedule data to the school dashboard digest

---

## Problem

Extracurricular activities (sports games and practices) for Ford, Jack, and Penn live in GameChanger. The morning, afternoon, and night digests have no visibility into these events. Parents miss upcoming games and practices because they aren't surfaced alongside school assignments and calendar events.

---

## Solution Overview

Add `gc` (the existing `bearyjd/gc` GameChanger scraper) as a vendor submodule. A new sync script (`gc-scrape.sh`) iterates all teams, fetches each schedule, and writes a single `gc-schedule.json` state file. The digest builder reads that file and surfaces a new "Extracurricular" card for upcoming events.

---

## Architecture

```
school-sync.sh
  └── sync/gc-scrape.sh
        ├── gc teams --json          → list all team IDs
        └── gc summary --json --team ID  (×N teams)
              ↓
        /app/state/gc-schedule.json

digest.py
  └── _load_gc_events(gc_path, days)
        ↓
  "⚽ Extracurricular" card in morning / afternoon / night digest
```

### Why a shell-loop aggregator

`gc summary --json` is intentionally per-team (one auth context, one API call per team). A wrapper script is the right aggregation layer — it mirrors the pattern already used for `run-digest.sh` and `run-weekly.sh`.

---

## New Files

| File | Purpose |
|------|---------|
| `vendor/gc/` | git submodule — `bearyjd/gc` |
| `sync/gc-scrape.sh` | Iterates teams, writes `gc-schedule.json` |

## Modified Files

| File | Change |
|------|--------|
| `sync/school-sync.sh` | Add gc-scrape step after Schoology |
| `school_dashboard/digest.py` | Add `gc_path` param + `_load_gc_events()` helper |
| `Dockerfile` | `pip install -e vendor/gc[browser]` |
| `config/env.example` + `.env.example` | Add `GC_EMAIL`, `GC_PASSWORD`, `GC_TEAM_MAP` |
| `CLAUDE.md` | Document gc integration |

---

## gc-schedule.json Schema

Written by `gc-scrape.sh`, read by `digest.py`. No transformation at scrape time — the file stores the gc output verbatim plus child attribution.

```json
{
  "scraped_at": "2026-04-12T06:00:00",
  "teams": [
    {
      "team_id": "abc123",
      "team_name": "SMCS 5th Grade Baseball",
      "child": "Ford",
      "schedule": [
        {
          "id": "evt_001",
          "date": "2026-04-14",
          "time": "16:00",
          "type": "practice",
          "opponent": "",
          "location": "Smith Field",
          "home_away": "home"
        },
        {
          "id": "evt_002",
          "date": "2026-04-16",
          "time": "18:00",
          "type": "game",
          "opponent": "St. Michael's",
          "location": "Away Field",
          "home_away": "away"
        }
      ]
    }
  ]
}
```

**Field notes:**
- `home_away` is a string (`"home"` or `"away"`), not a boolean — digest reader compares with `== "home"`
- `opponent` is empty string for practices
- `child` is derived from `GC_TEAM_MAP` at scrape time; if a team ID is not in the map, `team_name` is used as fallback

---

## Configuration

Three new env vars added to `config/env` (gitignored) and documented in `config/env.example`:

| Var | Required | Description | Example |
|-----|----------|-------------|---------|
| `GC_EMAIL` | yes | GameChanger login email | `jd@beary.us` |
| `GC_PASSWORD` | yes | GameChanger password | `...` |
| `GC_TEAM_MAP` | yes | `teamid:child` pairs, comma-separated | `abc123:Ford,def456:Jack,ghi789:Penn` |
| `SCHOOL_GC_PATH` | no | Override default path | defaults to `/app/state/gc-schedule.json` |

**One-time setup after deploy:** Run `gc teams --json` on the container to discover team IDs, then fill `GC_TEAM_MAP` in `config/env`. Re-run `gc-scrape.sh` to verify.

---

## sync/gc-scrape.sh

```
1. Load config/env
2. Run `gc teams --json` → list of {id, name, sport, season}
3. For each team:
   a. Run `gc summary --json --team {id}`
   b. Attach child name from GC_TEAM_MAP (fallback: team name)
   c. Append {team_id, team_name, child, schedule} to output
4. Write aggregated JSON to SCHOOL_GC_PATH
5. On any failure: log to /app/state/gc-scrape.log, send ntfy error alert, exit non-zero
```

Uses the same error-notification pattern as `run-digest.sh`:
- Persistent log at `/app/state/gc-scrape.log`
- ntfy `Title: GC Scrape FAILED` with `Priority: high` on failure

---

## school-sync.sh Integration

New step added after Schoology scrape, before state update:

```bash
# Scrape GameChanger schedules (non-fatal if gc is not configured)
if command -v gc &>/dev/null && [[ -n "${GC_EMAIL:-}" ]]; then
    bash /app/sync/gc-scrape.sh || true
fi
```

The `|| true` makes gc failure non-fatal — IXL and Schoology sync continue regardless.

---

## Digest Integration

### New parameter

`build_digest_text()`, `_build_morning_digest()`, `_build_night_digest()`, and `_build_afternoon_digest()` each receive:

```python
gc_path: str | None = os.environ.get("SCHOOL_GC_PATH", "/app/state/gc-schedule.json")
```

If the file doesn't exist or is malformed, the gc section is silently omitted.

### New helper

```python
def _load_gc_events(gc_path: str | None, days: int) -> list[dict]:
    """Return upcoming gc events within `days` days of today.
    
    Each item: {child, team_name, date, time, type, opponent, location, home_away}
    Returns [] if file missing, unreadable, or no events in window.
    """
```

### Digest windows

| Digest | Window | Shows |
|--------|--------|-------|
| Morning (7am) | today + next 2 days | "Ford: Practice today 4pm @ Smith Field" |
| Afternoon (3:30pm) | today only | Same-day events remaining |
| Night (8:30pm) | tomorrow only | "Jack: Game tomorrow vs. St. Michael's, Away" |
| Weekly Friday | next 7 days | Full week extracurricular preview |
| Weekly Sunday | next 7 days | Full week extracurricular preview |

### Card output example

```
⚽ Extracurricular

• Ford (Baseball): Practice today 4:00 PM @ Smith Field
• Jack (Soccer): Game Fri vs. St. Michael's, 6:00 PM, Away
• Penn (Lacrosse): Practice Sat 10:00 AM @ SMCS
```

If no events in the window, the card is omitted entirely (not shown as "No events").

---

## Dockerfile Changes

```dockerfile
# existing lines
COPY vendor/ixl-scrape/ ./vendor/ixl-scrape/
COPY vendor/schoology-scrape/ ./vendor/schoology-scrape/
COPY vendor/gc/ ./vendor/gc/          # NEW

RUN pip install --no-cache-dir -e ".[server]" \
    -e "vendor/ixl-scrape[browser]" \
    -e vendor/schoology-scrape \
    -e "vendor/gc[browser]"            # NEW — Playwright already installed
```

Playwright/Chromium already present in the image (installed for ixl-scrape), so no new system deps.

---

## Error Handling

| Failure | Behavior |
|---------|----------|
| `gc teams` fails (auth, network) | `gc-scrape.sh` exits non-zero, ntfy alert sent, old `gc-schedule.json` preserved if it exists |
| Individual team summary fails | Skip that team, log warning, continue with remaining teams |
| `gc-schedule.json` missing at digest time | gc card silently omitted, rest of digest unaffected |
| `gc-schedule.json` malformed JSON | gc card silently omitted, logged |
| `GC_EMAIL` not set | `school-sync.sh` skips gc step entirely, no error |

---

## Testing

- Unit test for `_load_gc_events()`: empty file, missing file, events in/out of window, child attribution from map
- Unit test for digest card rendering: gc events present, gc events absent, gc file missing
- No live credentials needed — mock `gc-schedule.json` fixture

---

## Deployment Sequence

1. Add `vendor/gc` submodule, commit
2. Update `Dockerfile`, `sync/school-sync.sh`, `digest.py`, env examples, `CLAUDE.md`
3. Push → CI → deploy to .14
4. On .14: run `gc teams --json` to discover team IDs
5. Add `GC_EMAIL`, `GC_PASSWORD`, `GC_TEAM_MAP` to `/opt/school/config/env`
6. Run `docker exec school-dashboard-1 bash /app/sync/gc-scrape.sh` to verify
7. Inspect `/opt/school/state/gc-schedule.json`
8. Run a manual morning digest to confirm the Extracurricular card appears
