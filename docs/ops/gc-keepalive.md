# GameChanger token keep-alive

## Why

- GameChanger login tokens last about 1 hour.
- With a saved Playwright session (`/root/.gc/sessions/playwright_context.json`),
  `gc token-refresh` renews an expired token silently, with no emailed code
  ("Saved session valid / Token refreshed via saved browser context").
- A full login needs an emailed MFA code, which is fragile (it timed out once at 06:10).
  So the saved session must never lapse: something has to refresh it often.
- GameChanger's WAF has blocked python/headless user agents since ~Aug 2026 (403).
  A host timer once ran a stale host copy of `gc` (0.1.25) and silently failed for
  months, so sessions lapsed. The timer now execs the `gc` inside the container.

## How it works

- Host timer `gc-token-refresh.timer` runs every 30 min and executes
  `docker exec school-dashboard-1 gc token-refresh` under `flock -o -w 900 /root/.gc/refresh.lock`.
- `sync/school-sync.sh` refreshes via `sync/gc-refresh.sh`, which takes the same lock
  (`flock -o -w ${GC_REFRESH_LOCK_WAIT:-600}`; `-o` stops gc's child processes inheriting the lock; override path with `GC_REFRESH_LOCK`).
  `/root/.gc` is bind-mounted into the container, so the lock works across host and container.
- The lock is held only around the refresh, not the scrape. Lock timeout or missing
  `flock` logs a WARN and the sync continues (the CLI also retries on 401).

## Installed drop-ins (reference copies in `docs/ops/systemd/`)

`/etc/systemd/system/gc-token-refresh.service.d/override.conf`:

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/flock -o -w 900 /root/.gc/refresh.lock /usr/bin/docker exec school-dashboard-1 gc token-refresh
TimeoutStartSec=1500
```

`/etc/systemd/system/gc-token-refresh.timer.d/override.conf`:

```ini
[Timer]
OnUnitActiveSec=
OnUnitActiveSec=30min
```

## Install / rollback

```bash
sudo mkdir -p /etc/systemd/system/gc-token-refresh.service.d /etc/systemd/system/gc-token-refresh.timer.d
sudo cp docs/ops/systemd/gc-token-refresh.service.d-override.conf /etc/systemd/system/gc-token-refresh.service.d/override.conf
sudo cp docs/ops/systemd/gc-token-refresh.timer.d-override.conf   /etc/systemd/system/gc-token-refresh.timer.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart gc-token-refresh.timer

# rollback
sudo rm /etc/systemd/system/gc-token-refresh.service.d/override.conf /etc/systemd/system/gc-token-refresh.timer.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart gc-token-refresh.timer
```

## Verify

```bash
systemctl list-timers gc-token-refresh.timer
journalctl -u gc-token-refresh -n 30 --no-pager      # expect "Token refreshed via saved browser context"
docker exec school-dashboard-1 school-state watchdog --dry-run
```

## Failure modes

- Container down: `docker exec` fails, the unit fails. Start the container; the next run recovers.
- Saved session expired or invalid: `gc` falls back to a full login, which needs the emailed
  OTP read through `gog` (Gmail). That requires `GOG_KEYRING_PASSWORD` in the container
  environment. Do NOT `source config/env` into a shell to run `gog`; it mangles the password.
- Lock held for the full wait (another refresh stuck): the flock gives up after 900 s
  (`TimeoutStartSec=1500` leaves room for that wait plus a ~10 min refresh);
  `school-sync.sh` logs a WARN after 600 s and continues.
