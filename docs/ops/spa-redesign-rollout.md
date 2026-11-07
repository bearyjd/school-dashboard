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
- [ ] Back up the host `web/` (it shadows the image, see below):
      `mkdir -p /root/school-backups && cp -a /opt/school/web /root/school-backups/web-pre-redesign`
- [ ] Take a state backup with `school-state transition backup` if state changes are planned at the same time.

## Deploy

Run on the Docker host. `IMG` is the image name from `docker-compose.yml` (set it as above).

1. `docker compose pull`
2. **Refresh the host `web/` copy.** The host directory `/opt/school/web/` shadows the image's
   `/app/web/`, so a new image alone keeps serving the old SPA and `app.py`. Copy the new image's
   files over it from a throwaway container:
   `docker run --rm -v /opt/school/web:/dest --entrypoint cp "$IMG" -a /app/web/. /dest/`
   (alternative: `docker cp school-dashboard-1:/app/web/. /opt/school/web/` after step 3, then
   `docker compose restart`). `cp -a` overwrites but never deletes, so old hashed files in
   `web/spa/dist/assets/` stay behind. They are harmless; to clear them use
   `rsync -a --delete` into a staging dir instead (copy the image files to `/tmp/web-new` with the
   command above, then `rsync -a --delete /tmp/web-new/ /opt/school/web/`).
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

Ask the parents to relabel the GameChanger team map entry from the run-together label (for example
`KidAKidB`) to the `+` form (`KidA+KidB`) in `config/env` (`GC_TEAM_MAP`). The `+` form lets the new
calendar attribute a shared team to both kids; with the old label those events show as "Unassigned": they appear only when no kid filter is set,
never under a kid's filter. The calendar covers this week and next (the data window).
Restart or wait for the next sync.

## Rollback

```
IMG=$(docker inspect --format '{{.Config.Image}}' school-dashboard-1); REPO=${IMG%%:*}
docker tag "$REPO:pre-redesign" "$REPO:latest"          # previous image back as latest
rm -rf /opt/school/web && cp -a /root/school-backups/web-pre-redesign /opt/school/web   # previous host web/
docker compose up -d --force-recreate
```

Installed PWAs return to the old layout after their service worker updates.

## Classic view retirement

The `/app/classic/*` routes and the "Classic view" links stay for two weeks after launch (launch
date + 14 days). Then remove `web/spa/src/classic/`, its routes in `App.tsx`, the `TopBar` and
Settings links, and the `/home` and `/child/:name` redirects; note it in `CHANGELOG.md`.

## Manual gate: Lighthouse

On the deployed `/app/` (mobile profile): performance >= 90, LCP < 2.5 s, CLS < 0.1. Not automated.
