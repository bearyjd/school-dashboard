#!/usr/bin/env bash
# school-sync.sh — Data refresh: scrape all sources, update state, regenerate dashboard.
# Runs via system cron at 6:00am and 2:30pm. No LLM needed.
# The 2:30pm run catches homework posted late by teachers.
#
# Usage:
#   ./school-sync.sh                      # uses defaults
#   IXL_CRON=/opt/ixl/cron/ixl-cron.sh ./school-sync.sh
#
# Crontab:
#   0 6 * * *    /opt/school-dashboard/school-sync.sh 2>>/tmp/school-sync.log
#   30 14 * * 1-5 /opt/school-dashboard/school-sync.sh 2>>/tmp/school-sync.log

set -euo pipefail

ENVFILE="${SCHOOL_DASHBOARD_ENV:-/etc/school-dashboard/env}"
[[ -f "$ENVFILE" ]] && { set -a; source "$ENVFILE"; set +a; }

# Cron does not inherit the Docker container ENV; re-export the defaults that
# match the in-container volume layout so school-state writes/reads the
# correct files.
# State dir mirrors school_dashboard/paths.py: SCHOOL_STATE_DIR, else the parent
# of SCHOOL_STATE_PATH, else /app/state. Per-file SCHOOL_* overrides still win.
STATE_DIR="${SCHOOL_STATE_DIR:-${SCHOOL_STATE_PATH:+$(dirname "$SCHOOL_STATE_PATH")}}"
STATE_DIR="${STATE_DIR:-/app/state}"
export SCHOOL_STATE_PATH="${SCHOOL_STATE_PATH:-$STATE_DIR/school-state.json}"
export SCHOOL_DB_PATH="${SCHOOL_DB_PATH:-$STATE_DIR/school.db}"
export SCHOOL_FACTS_PATH="${SCHOOL_FACTS_PATH:-$STATE_DIR/facts.json}"
export SCHOOL_EMAIL_DIGEST="${SCHOOL_EMAIL_DIGEST:-$STATE_DIR/email-digest.json}"
export SCHOOL_CALENDAR_PDF="${SCHOOL_CALENDAR_PDF:-$STATE_DIR/calendar.pdf}"
export SCHOOL_DASHBOARD_HTML="${SCHOOL_DASHBOARD_HTML:-$STATE_DIR/school-dashboard.html}"
export SCHOOL_GC_PATH="${SCHOOL_GC_PATH:-$STATE_DIR/gc-schedule.json}"

IXL_CRON="${IXL_CRON:-}"
IXL_DIR="${IXL_DIR:-/tmp/ixl}"
SGY_FILE="${SGY_FILE:-/tmp/schoology-daily.json}"
PS_FILE="${PS_FILE:-/tmp/powerschool-daily.json}"
# Where the sibling sync scripts live (override is for tests; default unchanged).
SYNC_SCRIPT_DIR="${SYNC_SCRIPT_DIR:-/app/sync}"

log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }
# Fail closed: a step runs only if `check` exits exactly 10 (inactive). Active (0),
# a crash (1), a missing binary (127) or any other code all skip.
maintenance_skip() {
    local rc=0
    school-state transition maintenance check >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 10 ] || return 1
    log "Maintenance mode active or check failed (rc=$rc) — skipping $1"
}

# Single-instance lock: a second sync (cron overlap, /api/sync, manual run) exits cleanly
# instead of scraping and merging concurrently. Taken BEFORE the in-flight marker so a
# skipped run never leaves one behind. The kernel drops the lock when this process exits.
# If the lock cannot be set up (no flock, unwritable dir) we warn and run unlocked rather
# than silently never syncing.
SYNC_LOCK="${SYNC_LOCK:-$STATE_DIR/sync.lock}"
if ! command -v flock &>/dev/null; then
    log "WARN: flock not found — running without a sync lock"
elif ! { mkdir -p "$(dirname "$SYNC_LOCK")" && exec 9>"$SYNC_LOCK"; } 2>/dev/null; then
    log "WARN: could not open sync lock $SYNC_LOCK — running without a sync lock"
elif ! flock -n 9; then
    log "INFO: another sync is already running (lock $SYNC_LOCK) — skipping"
    exit 0
fi

# Per-process in-flight marker, written BEFORE the maintenance check so a backup
# that sees no marker knows we have not yet passed the check. Removed on any exit.
SYNC_MARKER="$STATE_DIR/sync.running.$$"
trap 'rm -f "$SYNC_MARKER"' EXIT
{ mkdir -p "$(dirname "$SYNC_MARKER")" && echo "$$" > "$SYNC_MARKER"; } 2>/dev/null \
    || log "ERROR: could not write sync marker $SYNC_MARKER — a concurrent backup will not wait for this sync"

maintenance_skip "sync" && exit 0


# Helper: write sync metadata for a source (non-fatal)
write_sync_meta() {
    local source="$1" result="$2"
    local err rc=0
    err="$(python3 -c "import sys; from school_dashboard.sync_meta import write_sync_source; write_sync_source(sys.argv[1], sys.argv[2])" "$source" "$result" 2>&1)" || rc=$?
    if [[ "$rc" -ne 0 ]]; then
        log "WARN: could not record sync result for $source ($result) (rc=$rc): $(printf '%s' "$err" | tail -n 3 | tr '\n' ' ')"
    fi
    return 0
}

# Helper: per-source toggle. SOURCE_<NAME>_ENABLED defaults to enabled; 0/false/no/off disables.
source_enabled() {
    local var="SOURCE_${1^^}_ENABLED" val
    val="${!var:-}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    case "$val" in
        0|[Ff][Aa][Ll][Ss][Ee]|[Nn][Oo]|[Oo][Ff][Ff]) return 1 ;;
        *) return 0 ;;
    esac
}

# Helper: log + record a deliberately paused source (not a failure)
pause_source() {
    log "INFO: $1 paused (SOURCE_${1^^}_ENABLED is off) — skipping"
    write_sync_meta "$1" "paused"
}

# --- Step 1: IXL scrape ---
# Honor an external cron script if explicitly configured; otherwise drive the
# `ixl` CLI directly using ~/.ixl/accounts.env (one child per line:
# child_name:email:password). Mirrors _run_sync_background in web/app.py.
if ! source_enabled ixl; then
    pause_source ixl
elif [[ -n "$IXL_CRON" && -x "$IXL_CRON" ]]; then
    log "Running IXL scrape via $IXL_CRON..."
    if bash "$IXL_CRON"; then
        write_sync_meta "ixl" "ok"
    else
        log "WARN: IXL scrape had errors"
        write_sync_meta "ixl" "error"
    fi
elif command -v ixl &>/dev/null; then
    log "Running IXL scrape (per-child via ~/.ixl/accounts.env)..."
    mkdir -p "$IXL_DIR"
    rm -f "$IXL_DIR"/.*-assigned.??????  # sweep temp files left by a killed earlier run
    ixl_files=0
    ixl_failed=0
    accounts_env="${HOME:-/root}/.ixl/accounts.env"
    # Scrape one child into a private temp file; replace <slug>-assigned.json only on a
    # clean exit AND a non-error payload. On failure the previous file is REMOVED so the
    # merge skips the child (its old state entry keeps its true, old `updated`).
    # Only the error `code`/`stage` are logged, never message text (could echo credentials).
    IXL_CHECK='import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("invalid-json"); sys.exit(1)
if not isinstance(d, dict):
    print("invalid-json"); sys.exit(1)
errs = [e for e in (d.get("errors") or []) if isinstance(e, dict)]
if d.get("status") == "error" or errs or ("totals" not in d and "remaining" not in d):
    print(" ".join("code=%s stage=%s" % (e.get("code"), e.get("stage")) for e in errs) or "unexpected-payload")
    sys.exit(1)'
    ixl_scrape_child() {
        local label="$1" slug="$2" email="$3" password="$4" tmp rc=0 vrc=0 diag=""
        tmp="$(mktemp "$IXL_DIR/.${slug}-assigned.XXXXXX")" || { log "WARN: IXL scrape failed for $label (no temp file)"; return 1; }
        local -a tcmd=()
        command -v timeout &>/dev/null && tcmd=(timeout "${IXL_SCRAPE_TIMEOUT:-120}")
        IXL_EMAIL="$email" IXL_PASSWORD="$password" \
            "${tcmd[@]}" ixl assigned --json > "$tmp" 2>>"$STATE_DIR/school-sync.log" </dev/null || rc=$?
        diag="$(python3 -c "$IXL_CHECK" "$tmp" 2>/dev/null)" || vrc=$?
        if (( rc == 0 && vrc == 0 )); then
            mv -f "$tmp" "$IXL_DIR/${slug}-assigned.json" && return 0
            rc=1; diag="could not move scrape into place"
        fi
        rm -f "$tmp" "$IXL_DIR/${slug}-assigned.json"
        if (( rc == 124 )); then
            log "WARN: IXL scrape failed for $label (timed out after ${IXL_SCRAPE_TIMEOUT:-120}s)"
            return 1
        fi
        log "WARN: IXL scrape failed for $label (exit $rc)${diag:+: $diag}"
        return 1
    }
    if [[ -f "$accounts_env" ]]; then
        while IFS=: read -r child_name child_email child_password || [[ -n "$child_name" ]]; do
            child_name="${child_name// /}"
            [[ -z "$child_name" || "$child_name" == \#* ]] && continue
            slug="${child_name,,}"
            if ixl_scrape_child "$child_name" "$slug" "$child_email" "$child_password"; then
                ixl_files=$((ixl_files + 1))
            else
                ixl_failed=$((ixl_failed + 1))
            fi
        done < "$accounts_env"
    elif [[ -n "${IXL_EMAIL:-}" && -n "${IXL_PASSWORD:-}" ]]; then
        slug="$(echo "${IXL_EMAIL%@*}" | tr 'A-Z' 'a-z' | cut -d. -f1)"
        if ixl_scrape_child "${slug:-student}" "${slug:-student}" "$IXL_EMAIL" "$IXL_PASSWORD"; then
            ixl_files=1
        else
            ixl_failed=1
        fi
    fi
    if (( ixl_files > 0 && ixl_failed == 0 )); then
        write_sync_meta "ixl" "ok"
    elif (( ixl_files > 0 )); then
        log "WARN: IXL scrape failed for $ixl_failed child(ren); $ixl_files succeeded"
        write_sync_meta "ixl" "error"
    else
        log "WARN: IXL produced no per-child files"
        write_sync_meta "ixl" "error"
    fi
else
    log "WARN: ixl command not found — skipping"
fi

# --- Step 2: Schoology scrape ---
if ! source_enabled sgy; then
    pause_source sgy
elif command -v sgy &>/dev/null; then
    log "Running Schoology scrape..."
    # Write to a private temp file next to $SGY_FILE and replace it only on a clean exit AND
    # a non-empty JSON object, so a failed/truncated scrape never clobbers the last good one.
    # CLI stderr goes to a second temp file; only its last 20 lines are appended to the sync log.
    SGY_CHECK='import json, sys; d = json.load(open(sys.argv[1])); sys.exit(0 if isinstance(d, dict) and d else 1)'
    sgy_tmp=""
    sgy_err=""
    if sgy_tmp="$(mktemp "${SGY_FILE}.XXXXXX" 2>/dev/null)" \
        && sgy_err="$(mktemp "${STATE_DIR}/.sgy-stderr.XXXXXX" 2>/dev/null)" \
        && sgy summary --json > "$sgy_tmp" 2>"$sgy_err" \
        && python3 -c "$SGY_CHECK" "$sgy_tmp" 2>/dev/null; then
        mv "$sgy_tmp" "$SGY_FILE"
        write_sync_meta "sgy" "ok"
    else
        [[ -n "$sgy_tmp" ]] && rm -f "$sgy_tmp"
        [[ -n "$sgy_err" && -s "$sgy_err" ]] && tail -n 20 "$sgy_err" >> "$STATE_DIR/school-sync.log"
        log "WARN: SGY scrape had errors"
        write_sync_meta "sgy" "error"
    fi
    [[ -n "$sgy_err" ]] && rm -f "$sgy_err"
else
    log "WARN: sgy command not found — skipping"
fi

# --- Step 2a: PowerSchool scrape (optional; skipped cleanly if not installed/configured) ---
# Availability/credentials come from school_dashboard.powerschool.skip_reason (single source
# of truth). Output goes to a private mktemp file next to $PS_FILE and replaces it only when
# it is valid JSON without top-level warnings, so a bad scrape never clobbers the last good one.
PS_CHECK='import sys; from school_dashboard.powerschool import scrape_problem; p = scrape_problem(open(sys.argv[1]).read()); print(p); sys.exit(1 if p else 0)'
ps_problem=""
ps_tmp=""
if ! source_enabled powerschool; then
    pause_source powerschool
elif ! ps_skip="$(python3 -c 'from school_dashboard.powerschool import skip_reason; print(skip_reason())' 2>/dev/null)"; then
    log "INFO: powerschool skipped — availability check could not run"
elif [[ -n "$ps_skip" ]]; then
    log "INFO: powerschool skipped — $ps_skip"
else
    log "Running PowerSchool scrape..."
    if ps_tmp="$(mktemp "${PS_FILE}.XXXXXX" 2>/dev/null)" \
        && powerschool summary --json > "$ps_tmp" 2>>"$STATE_DIR/school-sync.log" \
        && ps_problem="$(python3 -c "$PS_CHECK" "$ps_tmp" 2>&1)"; then
        mv "$ps_tmp" "$PS_FILE"
        write_sync_meta "powerschool" "ok"
    else
        [[ -n "$ps_tmp" ]] && rm -f "$ps_tmp"
        log "WARN: PowerSchool scrape failed${ps_problem:+ ($ps_problem)}"
        write_sync_meta "powerschool" "error"
    fi
fi

# --- Step 2b: GameChanger scrape (non-fatal if not configured) ---
if ! source_enabled gc; then
    pause_source gc
elif command -v gc &>/dev/null && [[ -n "${GC_TOKEN:-}${GC_EMAIL:-}" ]]; then
    # Refresh first so the scrape starts with a hot user JWT. The CLI also
    # auto-recovers from 401 mid-flight (vendor/gc 0.1.30+), but doing this
    # upfront avoids a Playwright launch in the middle of the loop.
    log "Refreshing GC token..."
    # Lock held only around the refresh (shared with the host keep-alive timer).
    # A missing refresh script is non-fatal: warn and carry on (the CLI auto-recovers from 401).
    if [[ -f "$SYNC_SCRIPT_DIR/gc-refresh.sh" ]]; then
        bash "$SYNC_SCRIPT_DIR/gc-refresh.sh"
    else
        log "WARN: $SYNC_SCRIPT_DIR/gc-refresh.sh not found — skipping GC token refresh"
    fi

    log "Running GameChanger scrape..."
    if bash "$SYNC_SCRIPT_DIR/gc-scrape.sh"; then
        write_sync_meta "gc" "ok"
    else
        log "WARN: GC scrape had errors"
        write_sync_meta "gc" "error"
    fi
else
    log "INFO: gc not configured — skipping (set GC_TOKEN or GC_EMAIL in config/env)"
fi

# --- Step 3: Merge into state ---
maintenance_skip "state update" && exit 0
log "Updating state..."
school-state update --ixl-dir "$IXL_DIR" --sgy-file "$SGY_FILE" --ps-file "$PS_FILE" || {
    rc=$?
    if [ "$rc" -eq 3 ]; then log "Maintenance mode active — state update skipped"; exit 0; fi
    log "ERROR: State update failed — skipping remaining steps"; exit 1
}

# --- Step 4: Email sync (fetch, normalize, classify — no LLM) ---
if [[ -n "${SCHOOL_EMAIL_ACCOUNT:-}" ]]; then
    maintenance_skip "email sync" && exit 0
    log "Syncing emails..."
    school-state email-sync --account "$SCHOOL_EMAIL_ACCOUNT" || {
        rc=$?
        if [ "$rc" -eq 3 ]; then log "Maintenance mode active — email sync skipped"; exit 0; fi
        log "WARN: Email sync had errors"
    }
else
    log "WARN: SCHOOL_EMAIL_ACCOUNT not set — skipping email sync"
fi

# --- Step 5: Email intel (LLM extraction → school.db + facts.json) ---
# The school_dashboard.intel module was removed; skip cleanly until restored.
if python3 -c "import school_dashboard.intel" >/dev/null 2>&1; then
    if [[ -n "${LITELLM_URL:-}" && -f "$SCHOOL_EMAIL_DIGEST" ]]; then
        log "Running email intel extraction..."
        python3 -c "
import os, json, sys
from school_dashboard.intel import process_digest
digest_path = os.environ['SCHOOL_EMAIL_DIGEST']
with open(digest_path) as f:
    digest = json.load(f)
count = process_digest(
    digest=digest,
    db_path=os.environ['SCHOOL_DB_PATH'],
    facts_path=os.environ['SCHOOL_FACTS_PATH'],
    litellm_url=os.environ.get('LITELLM_URL', ''),
    api_key=os.environ.get('LITELLM_API_KEY', ''),
    model=os.environ.get('LITELLM_MODEL', 'claude-sonnet'),
)
print(f'Intel: {count} emails processed', flush=True)
" 2>&1 || log "WARN: Intel extraction had errors"
    else
        log "INFO: Skipping intel (LITELLM_URL not set or digest missing)"
    fi
else
    log "INFO: school_dashboard.intel module not available — skipping email intel step"
fi

# --- Step 6: Regenerate dashboard ---
maintenance_skip "dashboard regeneration" && exit 0
log "Regenerating dashboard..."
school-state html || {
    rc=$?
    if [ "$rc" -eq 3 ]; then log "Maintenance mode active — dashboard regeneration skipped"; exit 0; fi
    exit "$rc"
}

log "Sync complete."
