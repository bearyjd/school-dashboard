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

# State dir mirrors school_dashboard/paths.py: SCHOOL_STATE_DIR, else the parent
# of SCHOOL_STATE_PATH, else /app/state. Per-file SCHOOL_* overrides still win.
STATE_DIR="${SCHOOL_STATE_DIR:-${SCHOOL_STATE_PATH:+$(dirname "$SCHOOL_STATE_PATH")}}"
STATE_DIR="${STATE_DIR:-/app/state}"
LOGDIR="$STATE_DIR"
export SCHOOL_STATE_PATH="${SCHOOL_STATE_PATH:-$STATE_DIR/school-state.json}"
export SCHOOL_DB_PATH="${SCHOOL_DB_PATH:-$STATE_DIR/school.db}"
export SCHOOL_FACTS_PATH="${SCHOOL_FACTS_PATH:-$STATE_DIR/facts.json}"
export SCHOOL_GC_PATH="${SCHOOL_GC_PATH:-$STATE_DIR/gc-schedule.json}"
LOGFILE="$LOGDIR/digest-weekly.log"

TS=$(date -Iseconds)
# Skip while maintenance mode is active (backup/restore in progress).
# Fail closed: only an explicit "inactive" (exit 10) lets the script run; 0 means
# active, and any other code (crash, missing binary) is treated as active.
maint_rc=0; school-state transition maintenance check >/dev/null 2>&1 || maint_rc=$?
if [ "$maint_rc" -ne 10 ]; then
    echo "[$TS] weekly digest $MODE skipped (maintenance mode active or check failed, rc=$maint_rc)" >> "$LOGFILE"
    exit 0
fi

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
    gc_path=os.environ['SCHOOL_GC_PATH'],
)
send_ntfy(os.environ['NTFY_TOPIC'], text, title, cards=cards, db_path=os.environ['SCHOOL_DB_PATH'])
" >> "$LOGFILE" 2>&1; then
    echo "[$TS] weekly $MODE OK" >> "$LOGFILE"
else
    EXIT=$?
    echo "[$TS] weekly $MODE FAILED (exit $EXIT)" >> "$LOGFILE"
    curl -sf \
        -H "Title: Weekly Digest FAILED ($MODE)" \
        -H "Priority: high" \
        -H "Tags: warning" \
        -d "Weekly digest ($MODE) failed. Check $LOGFILE on the container." \
        "https://ntfy.sh/${NTFY_TOPIC}" 2>/dev/null || true
    exit $EXIT
fi
