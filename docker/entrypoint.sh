#!/usr/bin/env bash
set -euo pipefail

# Load env file if present
ENVFILE="${SCHOOL_DASHBOARD_ENV:-/app/config/env}"
[[ -f "$ENVFILE" ]] && { set -a; source "$ENVFILE"; set +a; }

# Start cron daemon
service cron start

# Start gunicorn. Exactly ONE worker process: sync state (running flag, lock, status, token rate limiter)
# lives in web/app.py memory. Concurrency comes from threads.
exec python3 -m gunicorn -w 1 --threads 8 --timeout 120 --graceful-timeout 8 -b 0.0.0.0:5000 web.app:app
