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

## Sync token: rotate it, then enter it once per device

Before this change the server put `SYNC_TOKEN` in every `/app/` and `/legacy` page, and installed PWAs cached that
page (service-worker cache `school-v2`). Treat the old token as compromised and **rotate it as part of this deploy**:

1. Generate a new one: `python3 -c "import secrets; print(secrets.token_hex(32))"`.
2. Put it in the server config as `SYNC_TOKEN=<value>` (`config/env`). Never paste it into chat, tickets or logs.
   The server ignores a `SYNC_TOKEN` shorter than 32 characters (`/api/sync` answers 501 and a WARNING is logged).
3. `docker compose up -d` to apply it. The new service worker (`school-v3`) deletes the old cached pages.
4. On each device that starts syncs from the app: Settings, field "Sync token", Save. Once per device.

Where it lives: the server keeps it only in `config/env`; it is never printed or served. A browser keeps its copy
only in that browser's `localStorage` (`school-sync-token`); "Clear" removes it, as does clearing site data. If the
browser blocks storage the token is kept in memory until the tab closes. The Android TWA install is a separate
browser profile and needs its own entry. Until entered, the Sync view says "Enter the sync token in Settings".

`POST /api/sync` still takes the `X-Sync-Token` header. Nothing in this repo's cron or `school-sync.sh` calls it
(they run the scrapers directly), so rotation affects only the app and any script you wrote yourself.
After 10 wrong tokens in a minute from one client address it answers 429 with `Retry-After`; a correct token is never
blocked by that. The counter is per process and keyed on the connecting address (IPv6 by /64); behind a reverse
proxy all clients share the proxy's address, so one device's typos can slow wrong guesses from the others for up to
a minute. The old `/legacy` page has no sync buttons; it points to Sync in `/app/`.

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

A roster child who had IXL data but whose IXL file is missing on the next update is recorded as `IXL <child>:
no scrape result was found`, and stays flagged until a good file arrives (it never expires, so a real long
outage keeps paging). When a child leaves IXL for good, set `"ixl": false` on that child in the roster config
(`school-dashboard.json`, `children.<Name>`); the flag must be `true` or `false` (anything else is ignored with a
warning). That child is then ignored by the quick check, digests, overview banner and the `scrape_degraded` rule.
The old `ixl` entry in `school-state.json` may stay. A child who never had IXL data is never flagged.

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

## Client address behind a reverse proxy (`TRUSTED_PROXY_COUNT`)

By default the `/api/sync` limiter keys on the TCP peer (`remote_addr`). Behind one reverse proxy that is the proxy, so all
clients share one bucket. Set `TRUSTED_PROXY_COUNT=1` (two chained proxies: `2`) to key on the `X-Forwarded-For` entry that
many places from the right instead. Entries further left are client-controlled and ignored; a missing, oversize or unparseable
header falls back to the peer address. Valid values are 0-10; anything else counts as 0.

Precondition: Flask's port must be reachable only through the proxy. In compose, publish it on the proxy interface
(for example `127.0.0.1:5000:5000` or the Tailscale/docker-network address), not `0.0.0.0`. Otherwise a client that connects
directly can send its own `X-Forwarded-For` and pick a fresh limiter bucket per request. The proxy must also append to (not
trust) the incoming header, which nginx (`$proxy_add_x_forwarded_for`), Caddy and Traefik do by default. This repo does not
edit any server compose file; make that change on the server first, then set the variable.

## Content-Security-Policy (report-only)

`/app/*` and `/legacy` send `Content-Security-Policy-Report-Only` (policy: `web/app.py::CSP_REPORT_ONLY`; nothing is blocked).
Browsers post violations to `/api/csp-report`, which only counts them: look for `CSP reports so far:` in the server log.
Enforcing needs the inline theme script in `web/spa/index.html` and the legacy page's inline scripts to move to nonces or files first.
