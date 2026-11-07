# SPA redesign rollout

What ships: the new SPA (Today, Tonight/Overdue/Missing/Events tables, Calendar, Settings with a
theme choice) backed by `GET /api/overview`, with the previous Home/Child views kept at
`/app/classic/home`. No data migrations; the API additions are read-only.

## Before deploy

- [ ] CI green on `main` (`pytest`, `spa` job). The `e2e` job is informational.
- [ ] Locally (optional but cheap): `npm --prefix web/spa run build && npm --prefix web/spa run e2e`.
- [ ] Tag the currently running image so it can be restored, e.g. `docker tag <image>:latest <image>:pre-redesign`.
- [ ] Take a backup: `school-state transition backup` (see maintenance docs) if state changes are planned at the same time.

## Deploy

1. `docker compose pull`
2. **Refresh the host `web/` copy.** The host bind-mounted `web/` (at `/opt/school/web/`) shadows
   the image's `/app/web/`, so a new image alone keeps serving the old SPA and `app.py`. Copy the
   image's files over it, using the host path as the destination and the image's `/app/web/` as the
   source (`cp -a /app/web/. /opt/school/web/` run where both are visible, for example in a
   throwaway container started from the new image with the host `web/` mounted at `/opt/school/web`).
3. `docker compose up -d`
4. `scripts/smoke-live.sh --container school-dashboard-1` (with `BASE_URL`, and `SYNC_TOKEN` if set).

## Verify

- [ ] Load `/app/` on a phone and a desktop: Today shows the kids, freshness tags and the Tonight list.
- [ ] Settings: switch Midnight / Daylight / System; it applies immediately and survives a reload.
- [ ] Installed PWA picks up the new service worker: the cache is now `school-v2` (old `school-v1`
      is deleted on activate). Close and reopen the app once if it still shows the old layout.
- [ ] Check off a manual item on Overdue; it stays done after refresh.
- [ ] `/app/classic/home` still opens, and the old `/app/home` and `/app/child/<name>` links redirect there.

## User action (server config, not code)

Ask the parents to relabel the GameChanger team map entry from `PennJack` to `Penn+Jack` in
`config/env` (`GC_TEAM_MAP`). The `+` form lets the new calendar attribute a shared team to both
kids; with the old label those events show as unassigned. Restart or wait for the next sync.

## Rollback

Retag the saved image back (`docker tag <image>:pre-redesign <image>:latest`), restore the host
`web/` copy from that image (same `cp -a` step), `docker compose up -d`. Installed PWAs return to the
old layout after the service worker updates.

## Classic view retirement

The `/app/classic/*` routes and the "Classic view" links stay for two weeks after launch (launch
date + 14 days). Then remove `web/spa/src/classic/`, its routes in `App.tsx`, the `TopBar` and
Settings links, and the `/home` and `/child/:name` redirects; note it in `CHANGELOG.md`.

## Manual gate: Lighthouse

On the deployed `/app/` (mobile profile): performance >= 90, LCP < 2.5 s, CLS < 0.1. Not automated.
