#!/usr/bin/env bash
set -euo pipefail

# Load env file if present
ENVFILE="${SCHOOL_DASHBOARD_ENV:-/app/config/env}"
[[ -f "$ENVFILE" ]] && { set -a; source "$ENVFILE"; set +a; }

# Start cron daemon
service cron start

# Start Flask web server
exec python3 -m flask --app web/app.py run --host=0.0.0.0 --port=5000
