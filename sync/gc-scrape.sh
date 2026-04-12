#!/usr/bin/env bash
# gc-scrape.sh — Scrape GameChanger schedules for all configured teams.
#
# Iterates all teams returned by `gc teams --json`, fetches each schedule via
# `gc summary --json --team ID`, then writes a single gc-schedule.json to
# SCHOOL_GC_PATH.  If GC_TEAM_MAP is set (format: "teamid:Child,..."), child
# names are resolved from the map; otherwise the team name is used as fallback.
#
# Usage: bash /app/sync/gc-scrape.sh
# Logs:  /app/state/gc-scrape.log
# ntfy:  "GC Scrape FAILED" (Priority: high) on failure
set -euo pipefail

ENVFILE="${SCHOOL_DASHBOARD_ENV:-/app/config/env}"
[[ -f "$ENVFILE" ]] && { set -a; source "$ENVFILE"; set +a; }

GC_PATH="${SCHOOL_GC_PATH:-/app/state/gc-schedule.json}"
LOGDIR="$(dirname "$GC_PATH")"
LOGFILE="$LOGDIR/gc-scrape.log"
TS=$(date -Iseconds)

log() { echo "[$TS] $*" >> "$LOGFILE"; echo "[$TS] $*" >&2; }

fail() {
    log "ERROR: $*"
    curl -sf \
        -H "Title: GC Scrape FAILED" \
        -H "Priority: high" \
        -H "Tags: warning" \
        -d "gc-scrape.sh failed: $*. Check $LOGFILE on the container." \
        "https://ntfy.sh/${NTFY_TOPIC:-}" 2>/dev/null || true
    exit 1
}

log "Starting gc scrape"

# --- Step 1: Fetch team list ---
log "Fetching team list..."
TEAMS_JSON=$(gc teams --json 2>>"$LOGFILE") || fail "gc teams failed (check auth: GC_TOKEN or GC_EMAIL/GC_PASSWORD)"

TEAM_COUNT=$(python3 -c "
import json, sys
teams = json.loads(sys.argv[1])
print(len(teams))
" "$TEAMS_JSON" 2>>"$LOGFILE" || echo "0")

log "Found $TEAM_COUNT team(s)"
[[ "$TEAM_COUNT" -eq 0 ]] && fail "No teams found — check GC_TOKEN or GC_EMAIL/GC_PASSWORD in config/env"

# --- Step 2: Parse GC_TEAM_MAP into lookup (bash associative array) ---
# Format: "teamid:ChildName,teamid2:ChildName2"
declare -A TEAM_MAP
if [[ -n "${GC_TEAM_MAP:-}" ]]; then
    IFS=',' read -ra PAIRS <<< "$GC_TEAM_MAP"
    for pair in "${PAIRS[@]}"; do
        tid="${pair%%:*}"
        child="${pair##*:}"
        [[ -n "$tid" && -n "$child" ]] && TEAM_MAP["$tid"]="$child"
    done
fi

# --- Step 3: Iterate teams, fetch summaries ---
OUTPUT_TEAMS="[]"

# Extract "id<TAB>name" lines from teams JSON
TEAM_LINES=$(python3 -c "
import json, sys
teams = json.loads(sys.argv[1])
for t in teams:
    tid = t.get('id', '')
    name = t.get('name', tid)
    if tid:
        print(tid + '\t' + name)
" "$TEAMS_JSON" 2>>"$LOGFILE")

while IFS=$'\t' read -r TEAM_ID TEAM_NAME; do
    [[ -z "$TEAM_ID" ]] && continue

    CHILD="${TEAM_MAP[$TEAM_ID]:-$TEAM_NAME}"
    log "Scraping: $TEAM_NAME ($TEAM_ID) → child=$CHILD"

    SUMMARY_JSON=$(gc summary --json --team "$TEAM_ID" 2>>"$LOGFILE") || {
        log "WARN: gc summary failed for team $TEAM_ID — skipping"
        continue
    }

    OUTPUT_TEAMS=$(python3 -c "
import json, sys
existing = json.loads(sys.argv[1])
summary  = json.loads(sys.argv[2])
existing.append({
    'team_id':   sys.argv[3],
    'team_name': sys.argv[4],
    'child':     sys.argv[5],
    'schedule':  summary.get('schedule', []),
})
print(json.dumps(existing))
" "$OUTPUT_TEAMS" "$SUMMARY_JSON" "$TEAM_ID" "$TEAM_NAME" "$CHILD" 2>>"$LOGFILE") || {
        log "WARN: Failed to parse summary for team $TEAM_ID — skipping"
        continue
    }
done <<< "$TEAM_LINES"

# --- Step 4: Write output ---
mkdir -p "$LOGDIR"
python3 -c "
import json, sys
print(json.dumps({'scraped_at': sys.argv[1], 'teams': json.loads(sys.argv[2])}, indent=2))
" "$TS" "$OUTPUT_TEAMS" > "$GC_PATH" || fail "Failed to write $GC_PATH"

WRITTEN=$(python3 -c "import json; d=json.load(open('$GC_PATH')); print(len(d['teams']))" 2>/dev/null || echo "?")
log "Done — $WRITTEN team(s) written to $GC_PATH"
