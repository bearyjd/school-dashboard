#!/usr/bin/env bash
# run-digest.sh — Wrapper for daily school-state digest jobs.
#
# Handles:
#   - env loading from config/env
#   - persistent logging to /app/state/ (survives container restarts)
#   - ntfy error notification if the digest command fails
#
# Usage (from cron):
#   bash /app/sync/run-digest.sh morning|afternoon|night
set -euo pipefail

MODE="${1:?usage: run-digest.sh morning|afternoon|night}"

ENVFILE="${SCHOOL_DASHBOARD_ENV:-/app/config/env}"
[[ -f "$ENVFILE" ]] && { set -a; source "$ENVFILE"; set +a; }

LOGDIR="${SCHOOL_STATE_PATH:-/app/state/school-state.json}"
LOGDIR="${LOGDIR%/*}"
LOGFILE="$LOGDIR/digest.log"

TS=$(date -Iseconds)

if school-state digest --mode "$MODE" >> "$LOGFILE" 2>&1; then
    echo "[$TS] digest --mode $MODE OK" >> "$LOGFILE"
else
    EXIT=$?
    echo "[$TS] digest --mode $MODE FAILED (exit $EXIT)" >> "$LOGFILE"
    curl -sf \
        -H "Title: Digest FAILED ($MODE)" \
        -H "Priority: high" \
        -H "Tags: warning" \
        -d "school-state digest --mode $MODE exited $EXIT. Check /app/state/digest.log on the container." \
        "https://ntfy.sh/${NTFY_TOPIC}" 2>/dev/null || true
    exit $EXIT
fi
