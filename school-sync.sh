#!/usr/bin/env bash
# school-sync.sh — Data refresh: scrape all sources, update state, regenerate dashboard.
# Runs via system cron at 6am. No LLM needed.
#
# Usage:
#   ./school-sync.sh                      # uses defaults
#   IXL_CRON=/opt/ixl/cron/ixl-cron.sh ./school-sync.sh
#
# Crontab:
#   0 6 * * * /opt/school-dashboard/school-sync.sh 2>/tmp/school-sync.log

set -euo pipefail

IXL_CRON="${IXL_CRON:-/opt/ixl/cron/ixl-cron.sh}"
IXL_DIR="${IXL_DIR:-/tmp/ixl}"
SGY_FILE="${SGY_FILE:-/tmp/schoology-daily.json}"

log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }

# --- Step 1: IXL scrape ---
if [[ -x "$IXL_CRON" ]]; then
    log "Running IXL scrape..."
    bash "$IXL_CRON" || log "WARN: IXL scrape had errors"
else
    log "WARN: IXL cron script not found at $IXL_CRON — skipping"
fi

# --- Step 2: Schoology scrape ---
if command -v sgy &>/dev/null; then
    log "Running Schoology scrape..."
    sgy summary --json > "$SGY_FILE" 2>/dev/null || log "WARN: SGY scrape had errors"
else
    log "WARN: sgy command not found — skipping"
fi

# --- Step 3: Merge into state ---
log "Updating state..."
school-state update --ixl-dir "$IXL_DIR" --sgy-file "$SGY_FILE"

# --- Step 4: Regenerate dashboard ---
log "Regenerating dashboard..."
school-state html

log "Sync complete."
