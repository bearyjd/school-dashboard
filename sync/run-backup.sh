#!/usr/bin/env bash
# run-backup.sh — Scheduled verified backup of the state dir, with retention.
#
# Handles:
#   - env loading from config/env
#   - maintenance mode: turned on only for the duration of the backup (backup
#     requires it), always turned off again; skipped if it is ALREADY active
#     (an operator is mid-operation) or the check itself fails (fail closed)
#   - backup into $STATE_DIR/backups/<UTC stamp>/ (0700; DB via the SQLite backup
#     API + integrity_check; data files only, never credentials), inside the
#     mounted state volume so it survives container recreation
#   - pruning to the newest $BACKUP_KEEP completed backups (default 14)
#   - logging to $STATE_DIR/backup.log and an ntfy alert on failure
#
# Usage (from cron):
#   bash /app/sync/run-backup.sh
set -euo pipefail

ENVFILE="${SCHOOL_DASHBOARD_ENV:-/app/config/env}"
[[ -f "$ENVFILE" ]] && { set -a; source "$ENVFILE"; set +a; }

STATE_DIR="${SCHOOL_STATE_DIR:-${SCHOOL_STATE_PATH:+$(dirname "$SCHOOL_STATE_PATH")}}"
STATE_DIR="${STATE_DIR:-/app/state}"
LOGFILE="$STATE_DIR/backup.log"
export SCHOOL_STATE_DIR="$STATE_DIR"

TS=$(date -Iseconds)
# Fail closed: only an explicit "inactive" (exit 10) lets the script run.
maint_rc=0; school-state transition maintenance check >/dev/null 2>&1 || maint_rc=$?
if [ "$maint_rc" -ne 10 ]; then
    echo "[$TS] backup skipped (maintenance mode active or check failed, rc=$maint_rc)" >> "$LOGFILE"
    exit 0
fi

alert_failure() {
    echo "[$TS] backup FAILED (exit $1)" >> "$LOGFILE"
    if [ -n "${NTFY_TOPIC:-}" ]; then
        curl -sf \
            -H "Title: Backup FAILED" \
            -H "Priority: high" \
            -H "Tags: warning" \
            -d "Scheduled state backup failed (exit $1). Check $LOGFILE on the container." \
            "https://ntfy.sh/${NTFY_TOPIC}" 2>/dev/null || true
    fi
}

maintenance_off() {
    school-state transition maintenance off >> "$LOGFILE" 2>&1 \
        || echo "[$TS] WARN: could not turn maintenance mode off; run: school-state transition maintenance off" >> "$LOGFILE"
}

rc=0
school-state transition maintenance on --reason "scheduled backup" >> "$LOGFILE" 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then
    alert_failure "$rc"
    exit "$rc"
fi

rc=0
school-state transition backup --wait-idle 300 >> "$LOGFILE" 2>&1 || rc=$?
maintenance_off
if [ "$rc" -eq 0 ]; then
    school-state transition prune-backups >> "$LOGFILE" 2>&1 || rc=$?
fi

if [ "$rc" -ne 0 ]; then
    alert_failure "$rc"
    exit "$rc"
fi
echo "[$TS] backup OK" >> "$LOGFILE"
