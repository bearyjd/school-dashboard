# Runbook

## Deployment

### First Deploy

```bash
git clone --recurse-submodules <repo>
cd school-dashboard
cp .env.example config/env        # fill in secrets
mkdir -p state
# drop 2025-2026 calendar PDF → state/calendar.pdf
gog auth add EMAIL                # one-time Google OAuth (host machine)
docker compose up -d
# Import calendar PDF into DB
docker compose exec dashboard python -m school_dashboard.calendar_import \
  /app/state/calendar.pdf /app/state/school.db
```

### Update

```bash
git pull --recurse-submodules
docker compose build
docker compose up -d
```

Template-only changes (`web/`) don't need a rebuild — the `web/` directory is volume-mounted.

## Health Checks

```bash
# Container running?
docker compose ps

# Flask responding?
curl http://localhost:5000/

# Cron logs (syncs)
docker compose exec dashboard tail -f /tmp/school-sync.log

# Digest logs
docker compose exec dashboard tail -f /tmp/digest.log
```

## Cron Schedule

| Time | Job | Log |
|------|-----|-----|
| 6:00am (weekdays) | Data sync (IXL → SGY → state → intel → HTML) | `/tmp/school-sync.log` |
| 2:30pm (weekdays) | Data re-sync | `/tmp/school-sync.log` |
| 7:00am (daily) | Morning digest → ntfy.sh | `/tmp/digest.log` |
| 3:30pm (weekdays) | Afternoon homework check → ntfy.sh | `/tmp/digest.log` |
| 8:30pm (daily) | Night prep digest → ntfy.sh | `/tmp/digest.log` |

## One-off Commands

```bash
# Force a digest manually
docker compose exec dashboard bash -c \
  'set -a && source /app/config/env && set +a && school-state digest --mode morning'

# Re-import calendar PDF
docker compose exec dashboard python -m school_dashboard.calendar_import \
  /app/state/calendar.pdf /app/state/school.db

# Run a manual data sync
docker compose exec dashboard bash /app/sync/school-sync.sh

# Open a shell inside the container
docker compose exec dashboard bash
```

## Common Issues

### Port 5000 Already in Use

```bash
fuser -k 5000/tcp
docker compose up -d
```

### Container Won't Start — Credential Dirs Missing

The compose file bind-mounts `/root/.config/gogcli`, `/root/.ixl`, `/root/.sgy` from the host. These must exist or Docker will create them as empty directories.

```bash
ls /root/.config/gogcli /root/.ixl /root/.sgy
# If missing, run credential setup on the host, then restart
gog auth add EMAIL
ixl init
sgy init
docker compose restart
```

### Digest Not Sending

1. Check `/tmp/digest.log` inside the container
2. Verify `NTFY_TOPIC` is set in `config/env`
3. Test ntfy manually: `curl -d "test" ntfy.sh/$NTFY_TOPIC`

### IXL Login Failing (Playwright/Cloudflare)

IXL uses Cloudflare; Playwright sessions expire (~60 min TTL). Check:

```bash
docker compose exec dashboard bash -c \
  'set -a && source /app/config/env && set +a && ixl summary --json 2>&1 | head -20'
```

If session expired, it will re-login automatically on the next attempt.

### Template Changes Not Appearing

`web/` is volume-mounted — changes should be live. If not, check the mount:

```bash
docker compose exec dashboard ls /app/web/templates/
```

If the mount is missing, rebuild and restart: `docker compose build && docker compose up -d`.

## Auto-Start on Reboot

The host crontab contains:

```
@reboot cd /opt/school/school-dashboard && docker compose up -d
```

Verify with `crontab -l` on the server.
