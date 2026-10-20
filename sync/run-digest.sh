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
# Skip while maintenance mode is active (backup/restore in progress).
# Fail closed: only an explicit "inactive" (exit 10) lets the script run; 0 means
# active, and any other code (crash, missing binary) is treated as active.
maint_rc=0; school-state transition maintenance check >/dev/null 2>&1 || maint_rc=$?
if [ "$maint_rc" -ne 10 ]; then
    echo "[$TS] digest --mode $MODE skipped (maintenance mode active or check failed, rc=$maint_rc)" >> "$LOGFILE"
    exit 0
fi

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
