# Weekly Digest Design

## Goal

Send two automated weekly ntfy notifications that replace manual dashboard checking:
- **Friday 3pm** — week in review: outstanding work, IXL progress, anything needing weekend attention
- **Sunday 7pm** — week ahead preview: assignments due next 7 days, calendar events, IXL targets

## Architecture

Extend `school_dashboard/digest.py` with a new `build_weekly_digest(mode, ...)` function alongside the existing morning digest. No new module needed — the weekly digest uses the same data sources, same LiteLLM call, same `send_ntfy()` delivery.

Two new cron entries in `docker/crontab` trigger the weekly sends independently from the daily sync — no scraping, just read current state files.

## Data Sources

Same as morning digest:
- `school-state.json` — Schoology assignments + IXL per-child status
- `school.db` — upcoming calendar events (next 7 days)
- `facts.json` — long-term facts
- Google Calendar via gcal (already in state)

## Function Signature

```python
def build_weekly_digest(
    mode: Literal["friday", "sunday"],
    state_path: str,
    db_path: str,
    facts_path: str,
    litellm_url: str,
    api_key: str,
    model: str,
    days_ahead: int = 7,
) -> str:
    ...
```

## LiteLLM Prompts

**Friday prompt context:**
- All pending/outstanding Schoology assignments per child
- IXL current remaining skills per child
- Any overdue items
- School events in the next 3 days
- Known facts

**Friday instruction:**
> "You are writing a Friday afternoon school summary for a parent. Summarize the week: what's still outstanding per child, IXL progress, anything that needs attention over the weekend. Be concise — 3-5 bullet points per child max."

**Sunday prompt context:**
- All Schoology assignments due in the next 7 days
- School calendar events next 7 days
- IXL remaining targets per child
- Known facts

**Sunday instruction:**
> "You are writing a Sunday evening school preview for a parent. Summarize what's coming up this week: assignments due with dates, school events, and IXL targets to hit. Be practical and forward-looking — help the parent plan."

## Delivery

```python
send_ntfy(topic, title="Week in Review", body=text)    # Friday
send_ntfy(topic, title="Week Ahead", body=text)         # Sunday
```

Same `NTFY_TOPIC` env var as morning digest.

## Cron Entries (docker/crontab)

```cron
# Weekly digests (Friday 3pm, Sunday 7pm)
0 15 * * 5 set -a && source /app/config/env && set +a && python3 -c "
from school_dashboard.digest import build_weekly_digest, send_ntfy
import os
text = build_weekly_digest('friday', os.environ['SCHOOL_STATE_PATH'], os.environ['SCHOOL_DB_PATH'], os.environ['SCHOOL_FACTS_PATH'], os.environ['LITELLM_URL'], os.environ['LITELLM_API_KEY'], os.environ['LITELLM_MODEL'])
send_ntfy(os.environ['NTFY_TOPIC'], 'Week in Review', text)
" >> /var/log/school-weekly.log 2>&1

0 19 * * 0 set -a && source /app/config/env && set +a && python3 -c "
from school_dashboard.digest import build_weekly_digest, send_ntfy
import os
text = build_weekly_digest('sunday', os.environ['SCHOOL_STATE_PATH'], os.environ['SCHOOL_DB_PATH'], os.environ['SCHOOL_FACTS_PATH'], os.environ['LITELLM_URL'], os.environ['LITELLM_API_KEY'], os.environ['LITELLM_MODEL'])
send_ntfy(os.environ['NTFY_TOPIC'], 'Week Ahead', text)
" >> /var/log/school-weekly.log 2>&1
```

## Testing

Add tests to `tests/test_digest.py`:
- `test_weekly_digest_friday_builds_text` — mock LiteLLM, verify friday mode returns non-empty string
- `test_weekly_digest_sunday_builds_text` — mock LiteLLM, verify sunday mode returns non-empty string
- `test_weekly_digest_empty_state` — graceful handling when state files missing

## Future: History Diff

Save a `state/monday-snapshot.json` each Monday morning. On Friday, diff current state against snapshot to show what actually changed (grades received, assignments completed). Not in this implementation.

## Files Changed

- `school_dashboard/digest.py` — add `build_weekly_digest()`
- `docker/crontab` — add Friday + Sunday cron entries
- `tests/test_digest.py` — add 3 tests
