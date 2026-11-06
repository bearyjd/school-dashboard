#!/usr/bin/env bash
# smoke-live.sh — post-deploy smoke test for a running school-dashboard server.
#
# Env:   BASE_URL (default http://localhost:5000), SYNC_TOKEN (optional, sent as
#        X-Sync-Token to GETs; never printed), MAX_AGE_HOURS (default 30).
# Flags: --container NAME   also check maintenance mode is off and `powerschool`
#                           is installed inside the docker container NAME.
# Output: a PASS/FAIL table (no child names, no tokens). Exit 1 on any FAIL.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:5000}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-30}"
CONTAINER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --container) CONTAINER="${2:?--container needs a name}"; shift 2 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

NAMES=()
RESULTS=()
DETAILS=()
FAILED=0

record() {  # name, PASS|FAIL, detail
    NAMES+=("$1"); RESULTS+=("$2"); DETAILS+=("$3")
    [[ "$2" == "PASS" ]] || FAILED=1
}

TMPDIR_SMOKE="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_SMOKE"' EXIT

fetch() {  # path -> body file; echoes HTTP status (000 on connection failure)
    local path="$1" args=(-s -o "$TMPDIR_SMOKE/${1//\//_}" -w '%{http_code}' --max-time 20)
    if [[ -n "${SYNC_TOKEN:-}" ]]; then args+=(-H "X-Sync-Token: ${SYNC_TOKEN}"); fi
    curl "${args[@]}" "${BASE_URL%/}${path}" 2>/dev/null || true
}

body_of() { echo "$TMPDIR_SMOKE/${1//\//_}"; }

is_json() { python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" 2>/dev/null; }

for path in /api/dashboard /api/readiness /api/sync/status /api/sync/meta; do
    code="$(fetch "$path")"
    code="${code:-000}"
    if [[ "$code" != "200" ]]; then
        record "GET $path" FAIL "HTTP $code"
    elif ! is_json "$(body_of "$path")"; then
        record "GET $path" FAIL "invalid JSON"
    else
        record "GET $path" PASS "200 JSON"
    fi
done

# Content checks only make sense if the fetch succeeded and parsed.
dash="$(body_of /api/dashboard)"
if is_json "$dash"; then
    if python3 - "$dash" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if isinstance(d.get("children"), list) and d["children"] else 1)
PY
    then record "dashboard children non-empty" PASS "roster present"
    else record "dashboard children non-empty" FAIL "children missing or empty"; fi

    if python3 - "$dash" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if "has_data" in d and "powerschool" in d else 1)
PY
    then record "dashboard has_data/powerschool keys" PASS "present"
    else record "dashboard has_data/powerschool keys" FAIL "key missing"; fi
else
    record "dashboard children non-empty" FAIL "no dashboard JSON"
    record "dashboard has_data/powerschool keys" FAIL "no dashboard JSON"
fi

meta="$(body_of /api/sync/meta)"
if is_json "$meta"; then
    while IFS=$'\t' read -r source verdict detail; do
        record "sync_meta $source" "$verdict" "$detail"
    done < <(python3 - "$meta" "$MAX_AGE_HOURS" <<'PY'
import json, sys
from datetime import datetime, timezone
meta = json.load(open(sys.argv[1]))
max_age = float(sys.argv[2])
if not isinstance(meta, dict) or not meta:
    print("(none)\tFAIL\tno sources recorded")
    sys.exit(0)
for source, entry in sorted(meta.items()):
    entry = entry if isinstance(entry, dict) else {}
    result = entry.get("last_result")
    if result == "paused":
        print(f"{source}\tPASS\tpaused")
        continue
    if result != "ok":
        print(f"{source}\tFAIL\tlast_result={result!r}")
        continue
    try:
        ts = datetime.fromisoformat(str(entry.get("last_run")))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    except ValueError:
        print(f"{source}\tFAIL\tunparseable last_run")
        continue
    if age > max_age:
        print(f"{source}\tFAIL\tlast_run {age:.1f}h old (max {max_age:g}h)")
    else:
        print(f"{source}\tPASS\tok, {age:.1f}h old")
PY
)
else
    record "sync_meta freshness" FAIL "no sync/meta JSON"
fi

if [[ -n "$CONTAINER" ]]; then
    rc=0
    docker exec "$CONTAINER" school-state transition maintenance check >/dev/null 2>&1 || rc=$?
    if [[ "$rc" -eq 10 ]]; then record "maintenance mode off" PASS "exit 10 (inactive)"
    else record "maintenance mode off" FAIL "check exit $rc (expected 10)"; fi

    if docker exec "$CONTAINER" sh -c 'command -v powerschool' >/dev/null 2>&1; then
        record "powerschool CLI in container" PASS "found"
    else
        record "powerschool CLI in container" FAIL "not found"
    fi
fi

printf '%-42s %-5s %s\n' CHECK RESULT DETAIL
for i in "${!NAMES[@]}"; do
    printf '%-42s %-5s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}" "${DETAILS[$i]}"
done
if [[ "$FAILED" -eq 0 ]]; then echo "SMOKE: PASS"; else echo "SMOKE: FAIL"; fi
exit "$FAILED"
