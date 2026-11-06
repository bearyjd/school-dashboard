#!/usr/bin/env bash
# run-watchdog.sh — Wrapper for the data-staleness watchdog (cron, after each sync).
#
# `school-state watchdog` pushes its own ntfy alerts. Exit codes: 0 healthy,
# 1 problems found (already alerted; not a script failure), 2 internal error
# (we alert about that here). Skips while maintenance mode is active.
#
# Usage (from cron):
#   bash /app/sync/run-watchdog.sh
set -euo pipefail

ENVFILE="${SCHOOL_DASHBOARD_ENV:-/app/config/env}"
[[ -f "$ENVFILE" ]] && { set -a; source "$ENVFILE"; set +a; }

STATE_DIR="${SCHOOL_STATE_DIR:-${SCHOOL_STATE_PATH:+$(dirname "$SCHOOL_STATE_PATH")}}"
STATE_DIR="${STATE_DIR:-/app/state}"
LOGFILE="$STATE_DIR/watchdog.log"

TS=$(date -Iseconds)
# Fail closed: only an explicit "inactive" (exit 10) lets the script run.
maint_rc=0; school-state transition maintenance check >/dev/null 2>&1 || maint_rc=$?
if [ "$maint_rc" -ne 10 ]; then
    echo "[$TS] watchdog skipped (maintenance mode active or check failed, rc=$maint_rc)" >> "$LOGFILE"
    exit 0
fi

rc=0; school-state watchdog >> "$LOGFILE" 2>&1 || rc=$?
case "$rc" in
    0) echo "[$TS] watchdog OK" >> "$LOGFILE" ;;
    1) echo "[$TS] watchdog: problems found (alert handled by watchdog)" >> "$LOGFILE" ;;
    *)
        echo "[$TS] watchdog FAILED (exit $rc)" >> "$LOGFILE"
        if [ -n "${NTFY_TOPIC:-}" ]; then
            curl -sf \
                -H "Title: Watchdog FAILED" \
                -H "Priority: high" \
                -H "Tags: warning" \
                -d "school-state watchdog exited $rc. Check $LOGFILE on the container." \
                "https://ntfy.sh/${NTFY_TOPIC}" 2>/dev/null || true
        else
            echo "[$TS] NTFY_TOPIC unset; cannot send the failure push" >> "$LOGFILE"
        fi
        exit "$rc"
        ;;
esac
