# GameChanger calendar-feed (ICS) fallback

When the GameChanger API path fails (login/WAF problems, expired token, empty
schedule), `sync/gc-scrape.sh` can fall back to each team's public calendar
feed. The feed answers any plain HTTP client with `text/calendar`: no login, no
browser, no WAF issue.

## 1. Get the link

In the GameChanger app or website: open the team, go to **Schedule**, then the
calendar / subscribe option, and copy the subscription link. It looks like
`webcal://api.team-manager.gc.com/ics-calendar-documents/user/<uuid>.ics?teamId=<teamId>&token=<secret>`.
You need the full `teamId` (the same id `gc teams --json` prints).

## 2. Configure

Create `/root/.gc/ics-feeds.json` on the server (inside the mounted `/root/.gc`;
override the location with `GC_ICS_FEEDS`):

```json
{
  "<full teamId>": "webcal://api.team-manager.gc.com/ics-calendar-documents/user/<uuid>.ics?teamId=<teamId>&token=<secret>"
}
```

```bash
chmod 600 /root/.gc/ics-feeds.json
```

Rules enforced by `school_dashboard/gc_ics.py`:

- `webcal://` is converted to `https://`; only host `api.team-manager.gc.com`
  over https is accepted (`GC_ICS_ALLOWED_HOSTS` extends the host list, for tests only).
- The `teamId` in the URL must equal the JSON key.
- The URL is validated against the parse of the prepared request URL (what
  requests/urllib3 actually connect to). Backslashes, whitespace, control or
  non-ASCII characters, userinfo (`user@`), a trailing dot and any port other
  than 443 are rejected. Plain http is accepted only for loopback hosts
  (127.0.0.1, localhost, ::1) named in `GC_ICS_ALLOWED_HOSTS` (tests only).
- No redirects, 20 s per-read timeout, 60 s total deadline, 2 MiB size cap.
- The config file is refused (feeds disabled, error in the log) if it is
  readable by group/other or owned by another user: run `chmod 600`.
- Feed text is untrusted: control characters are stripped, SUMMARY / LOCATION /
  calendar name are capped at 160 characters, and at most 500 events per feed
  are kept (a warning is logged when truncated).
- Recurring events (RRULE/RDATE/EXDATE) are not expanded: only the first
  occurrence is listed and a warning is logged. Events are keyed by
  (UID, RECURRENCE-ID); a cancelled event removes an earlier entry with the same
  key. Multi-day all-day events keep their start date only.
- Malformed events (bad dates, empty SUMMARY) are skipped and counted in the
  warnings, which `gc-scrape.sh` writes to its log.

## 3. Behaviour

Per team, if `gc summary` fails or returns no events and a feed is configured,
the schedule comes from `school-state gc-ics --team-id ID` and the log shows
`WARN: using calendar feed for team <id8> (api failed|empty)`. Each team in
`gc-schedule.json` carries `schedule_source: "api" | "ics"`. If the team list
itself fails or is empty, the teams with feeds are used instead (name from the
feed's `X-WR-CALNAME`, child from `GC_TEAM_MAP`). If every team came from
feeds the run still exits 0 and logs
`WARN: GC API unavailable; schedules from calendar feeds only`.

Alert: the fallback must not hide a broken login. When any team was served
from a feed, `gc-scrape.sh` runs `school-state gc-fallback-alert`, which sends
an ntfy "School: GameChanger login is failing" at most once per 24 h (state in
`gc-fallback-alert.json` in the state dir; needs `NTFY_TOPIC`). A failed send
never fails the scrape, and `sync_meta` still records `ok`.

Limits: feeds carry no opponent or home/away fields. Opponent is parsed from
`vs` / `@` in the title, otherwise the title is used; times are converted from
UTC to America/New_York.

Manual check: `school-state gc-ics --team-id <id>` prints JSON (exit 0), exit 3
if no feed is configured, exit 1 on fetch/parse failure.

## Security

- The link is a secret (the token grants read access to the team schedule).
  Never commit it, paste it in tickets/PRs, or put it in argv or logs. The code
  never prints it; errors show only the team id prefix and HTTP status.
- To revoke, regenerate the calendar link in GameChanger and update the file.
