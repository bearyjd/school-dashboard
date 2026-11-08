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
# A config that exists but cannot be used (bad perms/owner/JSON) exits 3: log a clean
# reason and alert (rate-limited), so failing teams are not silently dropped.
FEED_CONFIG_RC=0
python3 -c "
import sys
from school_dashboard import gc_ics
try:
    ids = list(gc_ics.load_feeds())
except gc_ics.ICSError as exc:
    print('WARN: calendar feeds disabled: ' + str(exc), file=sys.stderr)
    sys.exit(3)
for tid in ids:
    print(tid)
" > "$WORK/feed-ids.txt" 2>>"$LOGFILE" || FEED_CONFIG_RC=$?
if [[ "$FEED_CONFIG_RC" -ne 0 ]]; then
    : > "$WORK/feed-ids.txt"
    if [[ "$FEED_CONFIG_RC" -eq 3 ]]; then
        school-state gc-fallback-alert --config-problem 2>>"$LOGFILE" \
            || log "WARN: calendar feed config alert command failed"
    fi
fi
# Per-feed fetch cap (the Python deadline is the first line of defence; this is the backstop).
ICS_TIMEOUT_CMD=()
command -v timeout &>/dev/null && ICS_TIMEOUT_CMD=(timeout "${GC_ICS_TIMEOUT:-90}")
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

    warn_if_unmapped "$TEAM_ID" "${TEAM_NAME:-$TEAM_ID}"
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
        s = json.load(f).get('schedule')
    print(len(s) if isinstance(s, list) else -2)  # -2: no schedule list at all (renamed key?)
except (OSError, ValueError, AttributeError):
    print(-1)
" "$WORK/team-$N.json" 2>>"$LOGFILE") || API_EVENTS=-1
            [[ "$API_EVENTS" -eq -2 ]] && log "WARN: gc summary for team ${TEAM_ID:0:8} has no schedule list"
            [[ "$API_EVENTS" -le 0 ]] && REASON=empty
        else
            REASON=failed
        fi
    else
        REASON=failed
    fi

    if [[ -n "$REASON" ]]; then
        ICS_RC=1
        if has_feed "$TEAM_ID"; then
            ICS_RC=0
            "${ICS_TIMEOUT_CMD[@]}" school-state gc-ics --team-id "$TEAM_ID" > "$WORK/ics-$N.json" 2>>"$LOGFILE" \
                || ICS_RC=$?
            [[ "$ICS_RC" -eq 124 ]] && log "WARN: calendar feed for team ${TEAM_ID:0:8} timed out"
            [[ "$ICS_RC" -ne 0 ]] && log "WARN: calendar feed unusable for team ${TEAM_ID:0:8} (rc=$ICS_RC)"
        fi
        if [[ "$ICS_RC" -eq 0 ]]; then
            ICS_EVENTS=$(python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    print(len(json.load(f).get('schedule') or []))
" "$WORK/ics-$N.json" 2>>"$LOGFILE") || ICS_EVENTS=0
            # An API call that succeeded with no events is only overridden by a feed that has some.
            if [[ "$REASON" == failed || "$ICS_EVENTS" -gt 0 ]]; then
                log "WARN: using calendar feed for team ${TEAM_ID:0:8} (api $REASON)"
                mv -f "$WORK/ics-$N.json" "$WORK/team-$N.json"
                python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    for w in json.load(f).get('warnings') or []:
        print('WARN: calendar feed ' + sys.argv[2] + ': ' + str(w), file=sys.stderr)
" "$WORK/team-$N.json" "${TEAM_ID:0:8}" 2>>"$LOGFILE" || true
                SOURCE=ics
            fi
        fi
        if [[ "$SOURCE" == api && "$REASON" == failed ]]; then
            log "WARN: gc summary failed for team ${TEAM_ID:0:8} and no usable calendar feed — skipping"
            continue
        fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$N" "$TEAM_ID" "$TEAM_NAME" "$CHILD" "$SOURCE" >> "$WORK/manifest.tsv"
done < "$WORK/team-lines.tsv"

# --- Step 4: Build output from the files (single python call, no argv data) ---
mkdir -p "$LOGDIR"
COUNTS=$(python3 -c "
import json, sys
work, ts, out, prev_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    with open(prev_path) as f:
        prev = json.load(f)
    prev_teams = {t['team_id']: t for t in prev['teams'] if isinstance(t, dict)}
    prev_day = str(prev.get('scraped_at') or '')[:10]
except (OSError, ValueError, KeyError, TypeError, AttributeError):
    prev_teams, prev_day = {}, ''
teams = []
with open(work + '/manifest.tsv') as m:
    for line in m:
        try:
            n, tid, name, child, source = line.rstrip('\\n').split('\\t')
            with open(f'{work}/team-{n}.json') as f:
                summary = json.load(f)
            schedule = summary.get('schedule')
            missing = not isinstance(schedule, list)
            if missing:
                print(f'WARN: no schedule list for team {tid[:8]} — recording the failure', file=sys.stderr)
                schedule = []
        except (OSError, ValueError, AttributeError) as e:
            print(f'WARN: Failed to parse manifest/summary line {line[:40]!r} ({e}) — skipping', file=sys.stderr)
            continue
        name = name or summary.get('team_name') or tid
        entry = {'team_id': tid, 'team_name': name, 'child': child or name,
                 'schedule_source': source, 'schedule': schedule}
        if missing:
            # Keep this team's previous schedule (if any) and say so; never a silent empty one.
            old = prev_teams.get(tid) or {}
            old_sched = old.get('schedule')
            entry['schedule_error'] = 'missing_schedule'
            if isinstance(old_sched, list) and old_sched:
                entry['schedule'] = old_sched
                entry['schedule_source'] = old.get('schedule_source') or source
                entry['schedule_last_good'] = old.get('schedule_last_good') or prev_day
        teams.append(entry)
with open(out, 'w') as f:
    print(json.dumps({'scraped_at': ts, 'teams': teams}, indent=2), file=f)
fresh = [t for t in teams if 'schedule_error' not in t]
print(len(teams), sum(len(t['schedule']) for t in fresh), sum(t['schedule_source'] == 'ics' for t in teams))
" "$WORK" "$TS" "$GC_PATH.tmp.$$" "$GC_PATH" 2>>"$LOGFILE") || fail "Failed to write $GC_PATH"
read -r WRITTEN TOTAL_EVENTS ICS_TEAMS <<< "$COUNTS"

# Every team returning an empty schedule means the gc CLI 401'd silently
# (it returns exit 0 with empty data on auth failure). Treat that as a
# hard failure so the sync status reflects reality instead of
# overwriting a good gc-schedule.json with empty data.
if [[ "$TEAM_COUNT" -gt 0 && "${TOTAL_EVENTS:-0}" -eq 0 ]]; then
    if [[ "$API_TEAMS_OK" -eq 0 || "${ICS_TEAMS:-0}" -gt 0 ]]; then
        fail "GC API unavailable and calendar feeds empty. Refusing to overwrite $GC_PATH."
    fi
    fail "All $TEAM_COUNT team(s) returned empty schedules — likely auth failure (gc returns 200 with empty data on 401). Refusing to overwrite $GC_PATH."
fi

if [[ "${WRITTEN:-0}" -gt 0 && "${ICS_TEAMS:-0}" -eq "${WRITTEN:-0}" ]]; then
    log "WARN: GC API unavailable; schedules from calendar feeds only"
fi

# --- Step 5: Publish ---
# Re-check right before publishing: maintenance may have started during the scrape.
if maintenance_skip "gc-schedule write"; then exit 0; fi
mv -f "$GC_PATH.tmp.$$" "$GC_PATH" || fail "Failed to write $GC_PATH"

# The fallback must not hide a broken login: nudge (rate-limited, best effort) when any team used a feed.
if [[ "${ICS_TEAMS:-0}" -gt 0 ]]; then
    school-state gc-fallback-alert --ics-teams "$ICS_TEAMS" --total "$WRITTEN" 2>>"$LOGFILE" \
        || log "WARN: gc fallback alert command failed"
fi

log "Done — $WRITTEN team(s), $TOTAL_EVENTS event(s) written to $GC_PATH"
