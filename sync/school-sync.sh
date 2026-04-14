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

IXL_CRON="${IXL_CRON:-/opt/ixl/cron/ixl-cron.sh}"
IXL_DIR="${IXL_DIR:-/tmp/ixl}"
SGY_FILE="${SGY_FILE:-/tmp/schoology-daily.json}"

log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }

# Helper: write sync metadata for a source (non-fatal)
write_sync_meta() {
    local source="$1" result="$2"
    python3 -c "
from school_dashboard.sync_meta import write_sync_source
write_sync_source('${source}', '${result}')
" 2>/dev/null || true
}

# --- Step 1: IXL scrape ---
if [[ -x "$IXL_CRON" ]]; then
    log "Running IXL scrape..."
    if bash "$IXL_CRON"; then
        write_sync_meta "ixl" "ok"
    else
        log "WARN: IXL scrape had errors"
        write_sync_meta "ixl" "error"
    fi
else
    log "WARN: IXL cron script not found at $IXL_CRON — skipping"
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

# --- Step 5: Email intel (LiteLLM extraction → school.db + facts.json) ---
if [[ -n "${LITELLM_URL:-}" && -f "${SCHOOL_EMAIL_DIGEST:-/app/state/email-digest.json}" ]]; then
    log "Running email intel extraction..."
    python3 -c "
import os, json, sys, traceback
try:
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
except Exception as e:
    print(f'Intel error: {e}', file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
" 2>&1 || log "WARN: Intel extraction had errors"
else
    log "INFO: Skipping intel (LITELLM_URL not set or digest missing)"
fi

# --- Step 6: Regenerate dashboard ---
log "Regenerating dashboard..."
school-state html

log "Sync complete."
