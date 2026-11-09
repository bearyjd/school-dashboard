#!/usr/bin/env bash
# run-backup.sh — Scheduled verified backup of the state dir, with retention.
#
# Handles:
#   - env loading from config/env
#   - maintenance mode: `backup` requires it, so this wrapper turns it on for the
#     duration of the backup only. It remembers the operation_id it got and, on ANY
#     exit (normal, error, INT/TERM/HUP), clears the flag only if it still carries
#     that id, so an operator's or deploy's window is never removed. SIGKILL/OOM
#     cannot be trapped: the flag then stays on, and the watchdog's
#     `maintenance_stuck` rule alerts once it has been on for more than 2h.
#   - if maintenance is ALREADY on (or the check itself fails) the run is skipped
#     (fail closed) with a log line AND an ntfy alert
#   - backup into $STATE_DIR/backups/<UTC stamp>/ (0700; DB via the SQLite backup
#     API + integrity_check; data files only, never credentials), inside the
#     mounted state volume so it survives container recreation
#   - pruning to the newest $BACKUP_KEEP (default 14, must be an integer >= 1)
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
OP_ID=""    # operation_id of the maintenance window THIS run opened
CHILD=""    # pid of the foreground step, so a signal can stop it

notify() {  # notify TITLE MESSAGE
    if [ -n "${NTFY_TOPIC:-}" ]; then
        curl -sf \
            -H "Title: $1" \
            -H "Priority: high" \
            -H "Tags: warning" \
            -d "$2" \
            "https://ntfy.sh/${NTFY_TOPIC}" 2>/dev/null || true
    fi
}

alert_failure() {
    echo "[$TS] backup FAILED (exit $1)" >> "$LOGFILE"
    notify "Backup FAILED" "Scheduled state backup failed (exit $1). Check $LOGFILE on the container."
}

cleanup() {
    if [ -n "$OP_ID" ]; then
        local id="$OP_ID"; OP_ID=""
        school-state transition maintenance off --operation-id "$id" >> "$LOGFILE" 2>&1 \
            || echo "[$TS] WARN: could not clear maintenance mode; run: school-state transition maintenance off" >> "$LOGFILE"
    fi
}

on_signal() {  # on_signal EXITCODE: stop the running step, then exit (EXIT trap clears the flag)
    if [ -n "$CHILD" ]; then kill "$CHILD" 2>/dev/null || true; fi
    echo "[$TS] backup interrupted by signal" >> "$LOGFILE"
    exit "$1"
}

trap cleanup EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP

step() {  # step CMD...: run in the background and wait, so signals are handled promptly
    "$@" >> "$LOGFILE" 2>&1 &
    CHILD=$!
    local rc=0
    wait "$CHILD" || rc=$?
    CHILD=""
    return "$rc"
}

skip_with_alert() {
    echo "[$TS] backup skipped (maintenance mode active or check failed, rc=$1)" >> "$LOGFILE"
    notify "Backup SKIPPED" "Scheduled backup skipped: maintenance mode is on (or the check failed, rc=$1). Check $LOGFILE; clear with: school-state transition maintenance off"
    exit 0
}

# Fail closed: only an explicit "inactive" (exit 10) lets the script run.
maint_rc=0; school-state transition maintenance check >/dev/null 2>&1 || maint_rc=$?
[ "$maint_rc" -eq 10 ] || skip_with_alert "$maint_rc"

on_out=""; rc=0
on_out=$(school-state transition maintenance on --reason "scheduled backup" --json 2>> "$LOGFILE") || rc=$?
if [ "$rc" -ne 0 ]; then
    alert_failure "$rc"
    exit "$rc"
fi
# Someone else opened a window between the check and our `on`: it is not ours to clear.
case "$on_out" in *'"already_active": true'*) skip_with_alert "raced" ;; esac
OP_ID=$(printf '%s' "$on_out" | sed -n 's/.*"operation_id": *"\([A-Za-z0-9_-]*\)".*/\1/p')
if [ -z "$OP_ID" ]; then
    # we cannot prove which window is ours; refuse to clear anything blindly
    echo "[$TS] could not read the maintenance operation_id from: $on_out" >> "$LOGFILE"
    alert_failure 1
    exit 1
fi

rc=0
step school-state transition backup --wait-idle 300 || rc=$?
cleanup
if [ "$rc" -eq 0 ]; then
    step school-state transition prune-backups || rc=$?
fi

if [ "$rc" -ne 0 ]; then
    alert_failure "$rc"
    exit "$rc"
fi
echo "[$TS] backup OK" >> "$LOGFILE"
