# SPA redesign rollout

What ships: the new SPA (Today, Tonight/Overdue/Missing/Events tables, Calendar, Settings with a
theme choice) backed by `GET /api/overview`, with the previous Home/Child views kept at
`/app/classic/home`. Checks run before merge: screens (10 routes, 5 widths, both themes) and axe
(same routes at 375 and 1280 px, both themes; serious and critical violations fail). No data migrations; the API additions are read-only.

## Before deploy

- [ ] CI green on `main`: jobs `pytest` and `spa` (required) and `e2e` (informational, non-blocking).
- [ ] Locally (optional but cheap): `npm --prefix web/spa run build && npm --prefix web/spa run e2e`.
- [ ] Note the running image and tag it so it can be restored:
      `IMG=$(docker inspect --format '{{.Config.Image}}' school-dashboard-1); docker tag "$IMG" "${IMG%%:*}:pre-redesign"`
- [ ] Check the host compose file has **no** `./web:/app/web` mount (`! grep -q '/app/web' /opt/school/docker-compose.yml` succeeds; after a deploy, `docker inspect school-dashboard-1 --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}'` must not list `/app/web`).
      A mount shadows the image's `web/`, so a pull would not update the app. Only `state/` and `config/` are host-mounted.
- [ ] Take a state backup with `school-state transition backup` if state changes are planned at the same time.

## Deploy

Run on the Docker host. `IMG` is the image name from `docker-compose.yml` (set it as above).

1. `docker compose pull`
2. `docker compose up -d`
3. `scripts/smoke-live.sh --container school-dashboard-1` (with `BASE_URL`, and `SYNC_TOKEN` if set).

## Verify

- [ ] Load `/app/` on a phone and a desktop: Today shows the kids, freshness tags and the Tonight list.
- [ ] Settings: switch Midnight / Daylight / System; it applies immediately and survives a reload.
- [ ] Installed PWA picks up the new service worker: the cache is now `school-v2` (old `school-v1`
      is deleted on activate). Close and reopen the app once if it still shows the old layout.
- [ ] Check off a manual item on Overdue; it stays done after refresh.
- [ ] `/app/classic/home` still opens, and the old `/app/home` and `/app/child/<name>` links redirect there.

## User action (server config, not code)

Ask the parents to relabel the GameChanger team map entry from the run-together label (for example
`KidAKidB`) to the `+` form (`KidA+KidB`) in `config/env` (`GC_TEAM_MAP`). The `+` form lets the new
calendar attribute a shared team to both kids; with the old label those events show as "Unassigned": they appear only when no kid filter is set,
never under a kid's filter. The calendar covers this week and next (the data window).
Restart or wait for the next sync.

## Sync token: one-time entry per device

The server no longer puts `SYNC_TOKEN` in any page (`/app/` and `/legacy` are token-free). The secret lives only in
server config (`config/env`, variable `SYNC_TOKEN`) and is never printed or served. After this deploy every
device that starts syncs from the SPA must enter it once: open Settings, field "Sync token", Save. It is kept
in that browser's `localStorage` (`school-sync-token`) only; "Clear" removes it, and clearing site data does too.
Until entered, the Sync view says "Enter the sync token in Settings". Cron and scripts are unchanged
(`X-Sync-Token` header). `POST /api/sync` answers 429 with `Retry-After` after 10 wrong tokens in a minute from one
client address (per process; behind a reverse proxy all clients share the proxy's address, so one device's
typos can briefly lock out the rest, including cron, for up to a minute). The old `/legacy` page no longer has sync buttons; it points to Sync in `/app/`.

## Source banners and the watchdog

The "No successful sync yet" banner (a source that has never synced) depends on the watchdog: it needs
`first_seen`, which only `school-state watchdog` writes to `state/watchdog.json` (cron 8:30 and 16:30).
Until the watchdog has run once, a never-synced source shows no banner. The watchdog skips its run, and so
writes nothing, during maintenance mode and when `WATCHDOG_ENABLED=0`. Sources the watchdog does not monitor
(`SOURCE_*_ENABLED=0`, unconfigured GameChanger or PowerSchool) are shown as paused: no banner, and their rows
keep a muted "paused, last data as of ..." tag. Rows are not tagged for a never-synced source (it has none).

The overview banner also lists scrape problems written by `school-state update` into `scrape_meta`
(a bad IXL file, a failed Schoology child, an unusable Schoology scrape), for example
`Schoology: the last scrape returned unexpected data; showing the last good data from 2026-11-07 (unavailable for 2 days)`.
Once any entry has been unavailable for 24h the watchdog sends one `scrape_degraded` push per source
(`IXL scrape` / `Schoology scrape`), and a "Recovered" push when it clears. It follows the same switches as the
other watchdog rules (`WATCHDOG_ENABLED`, maintenance, `SOURCE_*_ENABLED`).

## Rollback

```
IMG=$(docker inspect --format '{{.Config.Image}}' school-dashboard-1); REPO=${IMG%%:*}
docker tag "$REPO:pre-redesign" "$REPO:latest"          # previous image back as latest
docker compose up -d --force-recreate
```

Do not run `docker compose pull` before rolling back, or `:latest` is overwritten again. The `pre-redesign` image serves the `web/` baked into it, which is not necessarily what the old host `web/` mount was serving at the time. Installed PWAs return to the old layout after their service worker updates.

## Classic view retirement

The `/app/classic/*` routes and the "Classic view" links stay for two weeks after launch (launch
date + 14 days). Then remove `web/spa/src/classic/`, its routes in `App.tsx`, the `TopBar` and
Settings links, and the `/home` and `/child/:name` redirects; note it in `CHANGELOG.md`.

## Manual gate: Lighthouse

On the deployed `/app/` (mobile profile): performance >= 90, LCP < 2.5 s, CLS < 0.1. Not automated.
