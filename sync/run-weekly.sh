#!/usr/bin/env bash
# run-weekly.sh — Wrapper for weekly digest jobs.
#
# Handles:
#   - env loading from config/env
#   - persistent logging to /app/state/ (survives container restarts)
#   - ntfy error notification if the digest fails
#
# Usage (from cron):
#   bash /app/sync/run-weekly.sh friday|sunday
set -euo pipefail

MODE="${1:?usage: run-weekly.sh friday|sunday}"

ENVFILE="${SCHOOL_DASHBOARD_ENV:-/app/config/env}"
[[ -f "$ENVFILE" ]] && { set -a; source "$ENVFILE"; set +a; }

LOGDIR="${SCHOOL_STATE_PATH%/*}"
LOGDIR="${LOGDIR:-/app/state}"
LOGFILE="$LOGDIR/digest-weekly.log"

TS=$(date -Iseconds)

if DIGEST_MODE="$MODE" python3 -c "
import os
from school_dashboard.digest import build_weekly_digest, send_ntfy
mode = os.environ['DIGEST_MODE']
title = 'Week in Review' if mode == 'friday' else 'Week Ahead'
text, cards = build_weekly_digest(
    mode,
    os.environ['SCHOOL_STATE_PATH'],
    os.environ['SCHOOL_DB_PATH'],
    os.environ['SCHOOL_FACTS_PATH'],
    os.environ['LITELLM_URL'],
    os.environ['LITELLM_API_KEY'],
    os.environ['LITELLM_MODEL'],
    gc_path=os.environ.get('SCHOOL_GC_PATH', '/app/state/gc-schedule.json'),
)
send_ntfy(os.environ['NTFY_TOPIC'], text, title, cards=cards, db_path=os.environ.get('SCHOOL_DB_PATH'))
" >> "$LOGFILE" 2>&1; then
    echo "[$TS] weekly $MODE OK" >> "$LOGFILE"
else
    EXIT=$?
    echo "[$TS] weekly $MODE FAILED (exit $EXIT)" >> "$LOGFILE"
    curl -sf \
        -H "Title: Weekly Digest FAILED ($MODE)" \
        -H "Priority: high" \
        -H "Tags: warning" \
        -d "Weekly digest ($MODE) failed. Check /app/state/digest-weekly.log on the container." \
        "https://ntfy.sh/${NTFY_TOPIC}" 2>/dev/null || true
    exit $EXIT
fi
