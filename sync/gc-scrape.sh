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

# Per-source toggle (mirrors source_enabled in school-sync.sh): paused => no scrape, no alert.
gc_toggle="${SOURCE_GC_ENABLED:-}"
gc_toggle="${gc_toggle#"${gc_toggle%%[![:space:]]*}"}"; gc_toggle="${gc_toggle%"${gc_toggle##*[![:space:]]}"}"
case "$gc_toggle" in
    0|[Ff][Aa][Ll][Ss][Ee]|[Nn][Oo]|[Oo][Ff][Ff]) echo "gc paused (SOURCE_GC_ENABLED off) — skipping" >&2; exit 0 ;;
esac

# State dir mirrors school_dashboard/paths.py: SCHOOL_STATE_DIR, else the parent
# of SCHOOL_STATE_PATH, else /app/state. Per-file SCHOOL_* overrides still win.
STATE_DIR="${SCHOOL_STATE_DIR:-${SCHOOL_STATE_PATH:+$(dirname "$SCHOOL_STATE_PATH")}}"
STATE_DIR="${STATE_DIR:-/app/state}"
GC_PATH="${SCHOOL_GC_PATH:-$STATE_DIR/gc-schedule.json}"
LOGDIR="$(dirname "$GC_PATH")"
LOGFILE="$LOGDIR/gc-scrape.log"
TS=$(date -Iseconds)

log() { echo "[$TS] $*" >> "$LOGFILE"; echo "[$TS] $*" >&2; }
# Fail closed: only an explicit "inactive" (exit 10) lets a step run; 0 means active
# and any other code (crash, missing binary) is treated as active.
maintenance_skip() {
    local rc=0
    school-state transition maintenance check >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 10 ] || return 1
    log "Maintenance mode active or check failed (rc=$rc) — skipping $1"
}

maintenance_skip "gc scrape" && exit 0

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

# Variable-size data (team list, per-team summaries) NEVER goes through argv:
# Linux caps one argv string at 128 KiB (MAX_ARG_STRLEN), which a real 11-team
# scrape exceeds. Everything lives in files under $WORK and is read by python.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/gc-scrape.XXXXXX")"
trap 'rm -rf "$WORK" "$GC_PATH.tmp.$$"' EXIT
trap 'exit 143' TERM INT

log "Starting gc scrape"

# --- Step 0: Teams that have a calendar-feed (ICS) fallback configured ---
# Ids only (never URLs/tokens). Missing or unreadable config => no fallbacks.
python3 -c "
import sys
from school_dashboard import gc_ics
for tid in gc_ics.load_feeds():
    print(tid)
" > "$WORK/feed-ids.txt" 2>>"$LOGFILE" || : > "$WORK/feed-ids.txt"
has_feed() { grep -Fxq -- "$1" "$WORK/feed-ids.txt"; }

# --- Step 1: Fetch team list ---
log "Fetching team list..."
API_TEAMS_OK=1
gc teams --json > "$WORK/teams.json" 2>>"$LOGFILE" || API_TEAMS_OK=0

# "id<TAB>name" lines
if [[ "$API_TEAMS_OK" -eq 1 ]]; then
python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    teams = json.load(f)
for t in teams:
    tid = t.get('id', '')
    name = t.get('name', tid)
    if tid:
        # tabs/newlines would corrupt the TSV handoff to the bash loop
        clean = lambda v: str(v).translate({9: ' ', 10: ' ', 13: ' '})
        print(clean(tid) + '\\t' + clean(name))
" "$WORK/teams.json" > "$WORK/team-lines.tsv" 2>>"$LOGFILE" || : > "$WORK/team-lines.tsv"
else
    : > "$WORK/team-lines.tsv"
fi
TEAM_COUNT=$(wc -l < "$WORK/team-lines.tsv" | tr -d ' ')

if [[ "$TEAM_COUNT" -eq 0 && -s "$WORK/feed-ids.txt" ]]; then
    # Team list failed or empty: iterate the teams that have a feed (name filled from the feed).
    log "WARN: team list unavailable or empty — iterating teams with calendar feeds"
    while IFS= read -r fid; do
        [[ -n "$fid" ]] && printf '%s\t%s\n' "$fid" "" >> "$WORK/team-lines.tsv"
    done < "$WORK/feed-ids.txt"
    TEAM_COUNT=$(wc -l < "$WORK/team-lines.tsv" | tr -d ' ')
    API_TEAMS_OK=0
fi

[[ "$API_TEAMS_OK" -eq 0 && "$TEAM_COUNT" -eq 0 ]] && fail "gc teams failed (check auth: GC_TOKEN or GC_EMAIL/GC_PASSWORD)"
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

# With a non-empty GC_TEAM_MAP, a team missing from it silently falls back to
# its team name as the child label — surface that so a typo'd ID is noticed.
warn_if_unmapped() {
    if [[ -n "${GC_TEAM_MAP:-}" && -z "${TEAM_MAP[$1]:-}" ]]; then
        log "WARN: team $1 ($2) not in GC_TEAM_MAP — using team name as child label"
    fi
}

# --- Step 3: Iterate teams, save each summary to a file ---
# manifest.tsv: n<TAB>team_id<TAB>team_name<TAB>child<TAB>source  (team-<n>.json holds the summary)
# source is "api", or "ics" when the calendar-feed fallback supplied the schedule.
: > "$WORK/manifest.tsv"
N=0
while IFS=$'\t' read -r TEAM_ID TEAM_NAME; do
    [[ -z "$TEAM_ID" ]] && continue

    [[ -n "$TEAM_NAME" ]] && warn_if_unmapped "$TEAM_ID" "$TEAM_NAME"
    CHILD="${TEAM_MAP[$TEAM_ID]:-$TEAM_NAME}"
    log "Scraping: ${TEAM_NAME:-$TEAM_ID} ($TEAM_ID) → child=${CHILD:-?}"

    N=$((N + 1))
    SOURCE=api
    REASON=""
    if [[ "$API_TEAMS_OK" -eq 1 ]]; then
        if gc summary --json --team "$TEAM_ID" > "$WORK/team-$N.json" 2>>"$LOGFILE"; then
            # Count events; -1 when the output is not a parseable summary.
            API_EVENTS=$(python3 -c "
import json, sys
try:
    with open(sys.argv[1]) as f:
        s = json.load(f).get('schedule') or []
    print(len(s) if isinstance(s, list) else 0)
except (OSError, ValueError, AttributeError):
    print(-1)
" "$WORK/team-$N.json" 2>>"$LOGFILE") || API_EVENTS=-1
            [[ "$API_EVENTS" -le 0 ]] && REASON=empty
        else
            REASON=failed
        fi
    else
        REASON=failed
    fi

    if [[ -n "$REASON" ]]; then
        if has_feed "$TEAM_ID" \
            && school-state gc-ics --team-id "$TEAM_ID" > "$WORK/ics-$N.json" 2>>"$LOGFILE"; then
            ICS_EVENTS=$(python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    print(len(json.load(f).get('schedule') or []))
" "$WORK/ics-$N.json" 2>>"$LOGFILE") || ICS_EVENTS=0
            # An API call that succeeded with no events is only overridden by a feed that has some.
            if [[ "$REASON" == failed || "$ICS_EVENTS" -gt 0 ]]; then
                log "WARN: using calendar feed for team ${TEAM_ID:0:8} (api $REASON)"
                mv -f "$WORK/ics-$N.json" "$WORK/team-$N.json"
                SOURCE=ics
            fi
        fi
        if [[ "$SOURCE" == api && "$REASON" == failed ]]; then
            log "WARN: gc summary failed for team $TEAM_ID — skipping"
            continue
        fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$N" "$TEAM_ID" "$TEAM_NAME" "$CHILD" "$SOURCE" >> "$WORK/manifest.tsv"
done < "$WORK/team-lines.tsv"

# --- Step 4: Build output from the files (single python call, no argv data) ---
mkdir -p "$LOGDIR"
COUNTS=$(python3 -c "
import json, sys
work, ts, out = sys.argv[1], sys.argv[2], sys.argv[3]
teams = []
with open(work + '/manifest.tsv') as m:
    for line in m:
        try:
            n, tid, name, child, source = line.rstrip('\\n').split('\\t')
            with open(f'{work}/team-{n}.json') as f:
                summary = json.load(f)
            schedule = summary.get('schedule') or []
            if not isinstance(schedule, list):
                print(f'WARN: non-list schedule for team {tid} — treating as empty', file=sys.stderr)
                schedule = []
        except (OSError, ValueError, AttributeError) as e:
            print(f'WARN: Failed to parse manifest/summary line {line[:40]!r} ({e}) — skipping', file=sys.stderr)
            continue
        name = name or summary.get('team_name') or tid
        teams.append({'team_id': tid, 'team_name': name, 'child': child or name,
                      'schedule_source': source, 'schedule': schedule})
with open(out, 'w') as f:
    print(json.dumps({'scraped_at': ts, 'teams': teams}, indent=2), file=f)
print(len(teams), sum(len(t['schedule']) for t in teams), sum(t['schedule_source'] == 'ics' for t in teams))
" "$WORK" "$TS" "$GC_PATH.tmp.$$" 2>>"$LOGFILE") || fail "Failed to write $GC_PATH"
read -r WRITTEN TOTAL_EVENTS ICS_TEAMS <<< "$COUNTS"

# Every team returning an empty schedule means the gc CLI 401'd silently
# (it returns exit 0 with empty data on auth failure). Treat that as a
# hard failure so the sync status reflects reality instead of
# overwriting a good gc-schedule.json with empty data.
if [[ "$TEAM_COUNT" -gt 0 && "${TOTAL_EVENTS:-0}" -eq 0 ]]; then
    fail "All $TEAM_COUNT team(s) returned empty schedules — likely auth failure (gc returns 200 with empty data on 401). Refusing to overwrite $GC_PATH."
fi

if [[ "${WRITTEN:-0}" -gt 0 && "${ICS_TEAMS:-0}" -eq "${WRITTEN:-0}" ]]; then
    log "WARN: GC API unavailable; schedules from calendar feeds only"
fi

# --- Step 5: Publish ---
# Re-check right before publishing: maintenance may have started during the scrape.
if maintenance_skip "gc-schedule write"; then exit 0; fi
mv -f "$GC_PATH.tmp.$$" "$GC_PATH" || fail "Failed to write $GC_PATH"

log "Done — $WRITTEN team(s), $TOTAL_EVENTS event(s) written to $GC_PATH"
