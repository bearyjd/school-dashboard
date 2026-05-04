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
export SCHOOL_STATE_PATH="${SCHOOL_STATE_PATH:-/app/state/school-state.json}"
export SCHOOL_DB_PATH="${SCHOOL_DB_PATH:-/app/state/school.db}"
export SCHOOL_FACTS_PATH="${SCHOOL_FACTS_PATH:-/app/state/facts.json}"
export SCHOOL_EMAIL_DIGEST="${SCHOOL_EMAIL_DIGEST:-/app/state/email-digest.json}"
export SCHOOL_CALENDAR_PDF="${SCHOOL_CALENDAR_PDF:-/app/state/calendar.pdf}"
export SCHOOL_DASHBOARD_HTML="${SCHOOL_DASHBOARD_HTML:-/app/state/school-dashboard.html}"
export SCHOOL_GC_PATH="${SCHOOL_GC_PATH:-/app/state/gc-schedule.json}"

IXL_CRON="${IXL_CRON:-}"
IXL_DIR="${IXL_DIR:-/tmp/ixl}"
SGY_FILE="${SGY_FILE:-/tmp/schoology-daily.json}"

log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }

# Helper: write sync metadata for a source (non-fatal)
write_sync_meta() {
    local source="$1" result="$2"
    python3 -c "import sys; from school_dashboard.sync_meta import write_sync_source; write_sync_source(sys.argv[1], sys.argv[2])" "$source" "$result" 2>/dev/null || true
}

# --- Step 1: IXL scrape ---
# Honor an external cron script if explicitly configured; otherwise drive the
# `ixl` CLI directly using ~/.ixl/accounts.env (one child per line:
# child_name:email:password). Mirrors _run_sync_background in web/app.py.
if [[ -n "$IXL_CRON" && -x "$IXL_CRON" ]]; then
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
    ixl_files=0
    accounts_env="${HOME:-/root}/.ixl/accounts.env"
    if [[ -f "$accounts_env" ]]; then
        while IFS=: read -r child_name child_email child_password || [[ -n "$child_name" ]]; do
            child_name="${child_name// /}"
            [[ -z "$child_name" || "$child_name" == \#* ]] && continue
            slug="${child_name,,}"
            if IXL_EMAIL="$child_email" IXL_PASSWORD="$child_password" \
                ixl assigned --json > "$IXL_DIR/${slug}-assigned.json" 2>>"/app/state/school-sync.log"; then
                ixl_files=$((ixl_files + 1))
            else
                log "WARN: IXL scrape failed for $child_name"
            fi
        done < "$accounts_env"
    elif [[ -n "${IXL_EMAIL:-}" && -n "${IXL_PASSWORD:-}" ]]; then
        slug="$(echo "${IXL_EMAIL%@*}" | tr 'A-Z' 'a-z' | cut -d. -f1)"
        if ixl assigned --json > "$IXL_DIR/${slug:-student}-assigned.json" 2>>"/app/state/school-sync.log"; then
            ixl_files=1
        fi
    fi
    if (( ixl_files > 0 )); then
        write_sync_meta "ixl" "ok"
    else
        log "WARN: IXL produced no per-child files"
        write_sync_meta "ixl" "error"
    fi
else
    log "WARN: ixl command not found — skipping"
fi

# --- Step 2: Schoology scrape ---
if command -v sgy &>/dev/null; then
    log "Running Schoology scrape..."
    if sgy summary --json > "$SGY_FILE" 2>/dev/null; then
        write_sync_meta "sgy" "ok"
    else
        log "WARN: SGY scrape had errors"
        write_sync_meta "sgy" "error"
    fi
else
    log "WARN: sgy command not found — skipping"
fi

# --- Step 2b: GameChanger scrape (non-fatal if not configured) ---
if command -v gc &>/dev/null && [[ -n "${GC_TOKEN:-}${GC_EMAIL:-}" ]]; then
    log "Running GameChanger scrape..."
    if bash /app/sync/gc-scrape.sh; then
        write_sync_meta "gc" "ok"
    else
        log "WARN: GC scrape had errors"
        write_sync_meta "gc" "error"
    fi
else
    log "INFO: gc not configured — skipping (set GC_TOKEN or GC_EMAIL in config/env)"
fi

# --- Step 3: Merge into state ---
log "Updating state..."
school-state update --ixl-dir "$IXL_DIR" --sgy-file "$SGY_FILE" || { log "ERROR: State update failed — skipping remaining steps"; exit 1; }

# --- Step 4: Email sync (fetch, normalize, classify — no LLM) ---
if [[ -n "${SCHOOL_EMAIL_ACCOUNT:-}" ]]; then
    log "Syncing emails..."
    school-state email-sync --account "$SCHOOL_EMAIL_ACCOUNT" || log "WARN: Email sync had errors"
else
    log "WARN: SCHOOL_EMAIL_ACCOUNT not set — skipping email sync"
fi

# --- Step 5: Email intel (LLM extraction → school.db + facts.json) ---
# The school_dashboard.intel module was removed; skip cleanly until restored.
if python3 -c "import school_dashboard.intel" >/dev/null 2>&1; then
    if [[ -n "${LITELLM_URL:-}" && -f "${SCHOOL_EMAIL_DIGEST:-/app/state/email-digest.json}" ]]; then
        log "Running email intel extraction..."
        python3 -c "
import os, json, sys
from school_dashboard.intel import process_digest
digest_path = os.environ.get('SCHOOL_EMAIL_DIGEST', '/app/state/email-digest.json')
with open(digest_path) as f:
    digest = json.load(f)
count = process_digest(
    digest=digest,
    db_path=os.environ.get('SCHOOL_DB_PATH', '/app/state/school.db'),
    facts_path=os.environ.get('SCHOOL_FACTS_PATH', '/app/state/facts.json'),
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
log "Regenerating dashboard..."
school-state html

log "Sync complete."
