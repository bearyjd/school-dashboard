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
- No redirects, 20 s timeout, 2 MiB size cap.
- A warning (no URL) is logged if the file is not mode 0600 or owned by another user.

## 3. Behaviour

Per team, if `gc summary` fails or returns no events and a feed is configured,
the schedule comes from `school-state gc-ics --team-id ID` and the log shows
`WARN: using calendar feed for team <id8> (api failed|empty)`. Each team in
`gc-schedule.json` carries `schedule_source: "api" | "ics"`. If the team list
itself fails or is empty, the teams with feeds are used instead (name from the
feed's `X-WR-CALNAME`, child from `GC_TEAM_MAP`). If every team came from
feeds the run still exits 0 and logs
`WARN: GC API unavailable; schedules from calendar feeds only`.

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
