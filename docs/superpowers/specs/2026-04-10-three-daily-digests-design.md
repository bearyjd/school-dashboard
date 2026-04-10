# Three Daily Digests Design

## Overview

Add three timed push notifications to the family school dashboard: a morning briefing (what does today hold), an afternoon homework check (did the kids do their work), and a night prep summary (what do we need ready for tomorrow). All three call LiteLLM with a focused prompt and push to the family's ntfy.sh topic. Morning and night run 7 days/week; afternoon runs weekdays only.

## Key Themes

### Three Digest Moments

| Digest | Time | Days | Question |
|--------|------|------|----------|
| Morning | 7:00am | Daily | What does today hold for the family? |
| Afternoon | 3:30pm | Weekdays | Did the kids complete their assigned work? |
| Night | 8:30pm | Daily | What do we need to be ready for tomorrow? |

**Morning** pulls today's GCal events, DB calendar events, Schoology assignments due today, IXL remaining work per child, email action items due today, and recurring facts. LiteLLM synthesizes a brief family briefing — what's happening, who needs what, anything urgent.

**Afternoon** pulls Schoology assignments due today and tomorrow, IXL remaining work per child, and email action items due today. Data is fresh because the 2:30pm weekday scraper run completes ~1 hour before this fires. LiteLLM reports what's done vs still pending, flags anything overdue.

**Night** pulls tomorrow's GCal events, DB calendar events, Schoology assignments due tomorrow, email action items due tomorrow, and facts relevant to tomorrow. LiteLLM summarizes what to have ready — gear, forms, early wake-up, anything to prepare.

### Shared GCal Module

GCal fetch logic currently lives inline in `web/app.py`. It moves to `school_dashboard/gcal.py` as a standalone function with its own in-process cache (15-min TTL, same as today). Both the Flask app and digest module import from this shared location.

### ntfy.sh Delivery

All three digests push to the existing `NTFY_TOPIC`. The existing `send_ntfy()` function gains a `title` parameter (currently hardcoded). Titles:

| Digest | ntfy Title |
|--------|-----------|
| Morning | `☀️ Today` |
| Afternoon | `📚 Homework Check` |
| Night | `🌙 Tomorrow` |

## Decisions & Positions

1. **LiteLLM for all three** — natural language synthesis ("Jack still has 4 IXL math skills and his reading log is due tomorrow") is more actionable than a raw structured list.
2. **Separate functions, not a unified engine** — `build_afternoon_digest()` and `build_night_digest()` added alongside existing `build_digest_text()`. The working morning digest is never touched.
3. **Afternoon at 3:30pm, not 2:30pm** — fires 1 hour after the 2:30pm scraper run so data is always fresh. If the scraper is late or slow, there is still a buffer.
4. **Morning/night run on weekends** — weekend sports and activities are real, afternoon homework check is weekdays only.
5. **Same ntfy topic for all three** — family shares one notification channel. No separate per-parent routing.
6. **GCal extracted to shared module** — avoids duplicating the subprocess call and cache logic across app.py and digest.py.

## Open Questions

None — all decisions resolved during design.

## Constraints & Boundaries

- This is NOT a new UI tab or dashboard view — purely push notification changes
- This is NOT changing the scraper schedule — data freshness depends on existing 6am/2:30pm runs
- This does NOT add weekend scraper runs — weekend digest uses last weekday's Schoology/IXL data (acceptable since school data doesn't change on weekends)
- This does NOT change the ntfy.sh topic or add per-child routing

## Code Changes

| File | Change |
|------|--------|
| `school_dashboard/gcal.py` | New module — `fetch_gcal_events(gog_account, days)` with 15-min cache |
| `school_dashboard/digest.py` | Add `build_afternoon_digest()`, `build_night_digest()`, update `send_ntfy()` with `title` param, update `build_digest_text()` to use gcal module |
| `web/app.py` | Import `fetch_gcal_events` from `school_dashboard.gcal` instead of inline |
| `docker/crontab` | Add 3 new cron entries (7am daily, 3:30pm weekdays, 8:30pm daily) |
| `school_dashboard/cli.py` | Add `--mode morning/afternoon/night` flag to digest subcommand |
