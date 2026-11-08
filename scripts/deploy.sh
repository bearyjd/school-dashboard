#!/usr/bin/env bash
# deploy.sh — pull-and-restart deploy with gates and automatic rollback.
# Encodes docs/ops/spa-redesign-rollout.md (that runbook stays the source of truth).
# Runs as root ON the Docker host. Never prints env values or tokens.
#
# Usage: scripts/deploy.sh [--slug NAME] [--smoke] [--dry-run]
#        scripts/deploy.sh --rollback TAG [--dry-run]
#   --slug NAME      label for the backup dir pre-<slug>-<UTC stamp> (default: deploy)
#   --smoke          also run scripts/smoke-live.sh as a gate (fails on any unhealthy source)
#   --rollback TAG   retag <repo>:TAG as :latest, recreate the container, re-verify
#   --dry-run        print every command that would run; touch nothing (no docker, curl or network)
# Env (defaults): DEPLOY_DIR=/opt/school  CONTAINER=school-dashboard-1  BASE_URL=http://127.0.0.1:5000
#   BACKUP_ROOT=/root/school-backups  MIN_FREE_GB=25  READY_TIMEOUT=90 (seconds)
#   KNOWN_WATCHDOG_PROBLEMS=source:issue,...  (watchdog problems that existed before; others fail the gate)
#   SMOKE_SCRIPT=<dir of this script>/smoke-live.sh
# Exit: 0 ok | 1 gate failed, rolled back and re-verified | 2 refused or bad arguments | 3 rollback itself failed
set -euo pipefail
umask 077

SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/school}"
CONTAINER="${CONTAINER:-school-dashboard-1}"
BASE_URL="${BASE_URL:-http://127.0.0.1:5000}"
BACKUP_ROOT="${BACKUP_ROOT:-/root/school-backups}"
MIN_FREE_GB="${MIN_FREE_GB:-25}"
READY_TIMEOUT="${READY_TIMEOUT:-90}"
KNOWN_WATCHDOG_PROBLEMS="${KNOWN_WATCHDOG_PROBLEMS:-}"
SMOKE_SCRIPT="${SMOKE_SCRIPT:-$SELF_DIR/smoke-live.sh}"

SLUG="deploy"; SMOKE=0; DRY=0; ROLLBACK_TAG=""; ROLLBACK_SET=0

die() { echo "deploy.sh: $*" >&2; exit 2; }

NAME_RE='^[A-Za-z0-9._-]+$'
valid_name() { [[ "$1" =~ $NAME_RE && ${#1} -le 100 ]]; }

# ---- arguments -----------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --slug) [[ $# -ge 2 ]] || die "--slug needs a value"; SLUG="$2"; shift 2 ;;
        --rollback) [[ $# -ge 2 ]] || die "--rollback needs a tag"; ROLLBACK_TAG="$2"; ROLLBACK_SET=1; shift 2 ;;
        --smoke) SMOKE=1; shift ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

valid_name "$SLUG" || die "--slug must match $NAME_RE"
if [[ "$ROLLBACK_SET" -eq 1 ]]; then valid_name "$ROLLBACK_TAG" || die "--rollback tag must match $NAME_RE"; fi
[[ "$CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "invalid CONTAINER"
[[ "$BASE_URL" =~ ^https?://[A-Za-z0-9.:_-]+(/[A-Za-z0-9._~/-]*)?$ ]] || die "invalid BASE_URL"
[[ "$MIN_FREE_GB" =~ ^[0-9]+$ ]] || die "MIN_FREE_GB must be an integer"
[[ "$READY_TIMEOUT" =~ ^[0-9]+$ ]] || die "READY_TIMEOUT must be an integer"
[[ "$KNOWN_WATCHDOG_PROBLEMS" =~ ^[A-Za-z0-9_:.,-]*$ ]] || die "invalid KNOWN_WATCHDOG_PROBLEMS"
[[ "$DEPLOY_DIR" == /* && "$DEPLOY_DIR" =~ ^[A-Za-z0-9._/-]+$ ]] || die "DEPLOY_DIR must be a plain absolute path"
[[ "$BACKUP_ROOT" == /* && "$BACKUP_ROOT" =~ ^[A-Za-z0-9._/-]+$ ]] || die "BACKUP_ROOT must be a plain absolute path"
BASE_URL="${BASE_URL%/}"

STATE_DIR="$DEPLOY_DIR/state"
ENV_FILE="$DEPLOY_DIR/config/env"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$BACKUP_ROOT/pre-$SLUG-$STAMP"
TMP=""

if [[ "$DRY" -eq 0 ]]; then
    TMP="$(mktemp -d)"
    trap 'rm -rf -- "$TMP"' EXIT
fi

# ---- helpers -------------------------------------------------------------------
run() {  # print in dry-run, execute otherwise
    if [[ "$DRY" -eq 1 ]]; then
        printf '[dry-run]'; printf ' %q' "$@"; printf '\n'
    else
        "$@"
    fi
}
note() { printf '%s\n' "$*"; }
dc() { run docker compose -f "$COMPOSE_FILE" --project-directory "$DEPLOY_DIR" "$@"; }

preflight_sync() {
    shopt -s nullglob
    local markers=("$STATE_DIR"/sync.running.*)
    shopt -u nullglob
    [[ ${#markers[@]} -eq 0 ]] || die "a sync is running (state/sync.running.* present); wait for it"
}

preflight() {
    if [[ "$DRY" -eq 1 ]]; then
        note "[dry-run] preflight: no $STATE_DIR/sync.running.*, no $STATE_DIR/maintenance.json, >= ${MIN_FREE_GB}G free on $DEPLOY_DIR, env and compose files exist, compose has no /app/web mount"
        return
    fi
    preflight_sync
    [[ ! -e "$STATE_DIR/maintenance.json" ]] || die "maintenance mode is on; turn it off first"
    [[ -f "$ENV_FILE" && -f "$COMPOSE_FILE" ]] || die "missing $ENV_FILE or $COMPOSE_FILE"
    if grep -q '/app/web' "$COMPOSE_FILE"; then die "compose file mounts /app/web; it would shadow the image (see runbook)"; fi
    local avail_kb
    avail_kb="$(df -Pk -- "$DEPLOY_DIR" | awk 'NR==2 {print $4}')"
    [[ "$avail_kb" =~ ^[0-9]+$ ]] || die "cannot read free disk space"
    (( avail_kb >= MIN_FREE_GB * 1024 * 1024 )) || die "less than ${MIN_FREE_GB}G free on $DEPLOY_DIR"
}

wait_ready() {
    [[ "$DRY" -eq 0 ]] || { note "[dry-run] wait up to ${READY_TIMEOUT}s for GET /api/sync/status = 200"; return 0; }
    local waited=0 code
    while :; do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE_URL/api/sync/status" 2>/dev/null || true)"
        [[ "$code" == "200" || "$waited" -ge "$READY_TIMEOUT" ]] && return 0
        sleep 2; waited=$((waited + 2))
    done
}

FAILS=0
gate() {  # name, PASS|FAIL
    printf '  %-4s %s\n' "$2" "$1"
    [[ "$2" == PASS ]] || FAILS=$((FAILS + 1))
}

http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE_URL$1" 2>/dev/null || true; }
cache_name() { grep -o "CACHE = '[^']*'" -- "$1" | head -n1 || true; }

run_gates() {  # sets FAILS; $1 = ISO time the (re)start began; $2 = with-smoke (0|1)
    local since="$1" with_smoke="$2" p code out served built n rc
    FAILS=0
    if [[ "$DRY" -eq 1 ]]; then
        note "[dry-run] gates: GET /api/dashboard /api/overview /api/readiness /api/sync/status /api/sync/meta /app/ /legacy = 200;"
        note "[dry-run]        GET / = 302 -> /app/; served /app/sw.js cache name == built dist/sw.js in $CONTAINER;"
        note "[dry-run]        docker exec $CONTAINER school-state transition maintenance check = exit 10;"
        note "[dry-run]        docker logs --since <start> $CONTAINER has 0 tracebacks;"
        note "[dry-run]        docker exec $CONTAINER school-state watchdog --dry-run --json: only KNOWN_WATCHDOG_PROBLEMS"
        [[ "$with_smoke" -eq 0 ]] || note "[dry-run]        $SMOKE_SCRIPT --container $CONTAINER"
        return 0
    fi
    for p in /api/dashboard /api/overview /api/readiness /api/sync/status /api/sync/meta /app/ /legacy; do
        code="$(http_code "$p")"
        if [[ "$code" == "200" ]]; then gate "GET $p" PASS; else gate "GET $p (HTTP ${code:-000})" FAIL; fi
    done
    out="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 20 "$BASE_URL/" 2>/dev/null || true)"
    if [[ "${out%% *}" == "302" && "${out#* }" == */app/ ]]; then gate "GET / -> 302 /app/" PASS; else gate "GET / -> 302 /app/ (got ${out%% *})" FAIL; fi

    curl -s -o "$TMP/sw.served" --max-time 20 "$BASE_URL/app/sw.js" 2>/dev/null || true
    docker exec -- "$CONTAINER" cat /app/web/spa/dist/sw.js > "$TMP/sw.built" 2>/dev/null || true
    served="$(cache_name "$TMP/sw.served")"; built="$(cache_name "$TMP/sw.built")"
    if [[ -n "$served" && "$served" == "$built" ]]; then gate "service worker cache name matches build" PASS; else gate "service worker cache name matches build" FAIL; fi

    rc=0; docker exec -- "$CONTAINER" school-state transition maintenance check >/dev/null 2>&1 || rc=$?
    if [[ "$rc" -eq 10 ]]; then gate "maintenance gate exit 10" PASS; else gate "maintenance gate (exit $rc, expected 10)" FAIL; fi

    n="$( { docker logs --since "$since" "$CONTAINER" 2>&1 || true; } | grep -c 'Traceback' || true)"
    if [[ "$n" == "0" ]]; then gate "no tracebacks in container log" PASS; else gate "tracebacks in container log: $n" FAIL; fi

    docker exec -- "$CONTAINER" school-state watchdog --dry-run --json > "$TMP/wd.json" 2>/dev/null || true
    n="$(python3 -I - "$TMP/wd.json" "$KNOWN_WATCHDOG_PROBLEMS" <<'PY' || echo bad
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    known = {k for k in sys.argv[2].split(",") if k}
    print(sum(1 for p in data["problems"] if f'{p["source"]}:{p["issue"]}' not in known))
except Exception:
    print("bad")
PY
)"
    if [[ "$n" == "0" ]]; then gate "watchdog: only known problems" PASS; else gate "watchdog: unknown problems/unreadable ($n)" FAIL; fi

    if [[ "$with_smoke" -eq 1 ]]; then
        if BASE_URL="$BASE_URL" "$SMOKE_SCRIPT" --container "$CONTAINER" >/dev/null 2>&1; then gate "smoke-live" PASS; else gate "smoke-live" FAIL; fi
    fi
    [[ "$FAILS" -eq 0 ]]
}

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

resolve_image() {  # sets REPO and IMAGE_ID
    if [[ "$DRY" -eq 1 ]]; then REPO="IMAGE_REPO"; IMAGE_ID="RUNNING_IMAGE_ID"; return; fi
    local img last
    img="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER")"
    IMAGE_ID="$(docker inspect --format '{{.Image}}' "$CONTAINER")"
    last="${img##*/}"
    if [[ "$last" == *:* ]]; then REPO="${img%:*}"; else REPO="$img"; fi
    [[ "$REPO" =~ ^[A-Za-z0-9._/:-]+$ ]] || die "unexpected image name"
    [[ "$IMAGE_ID" =~ ^[A-Za-z0-9:]+$ ]] || die "unexpected image id"
}

restore_and_verify() {  # $1 = full image ref to restore as :latest
    note "ROLLBACK: restoring $1 as ${REPO}:latest"
    run docker tag "$1" "$REPO:latest"
    dc up -d --force-recreate
    local since; since="$(now_iso)"
    wait_ready
    if run_gates "$since" 0; then note "ROLLBACK: verified"; return 0; fi
    note "ROLLBACK: verification FAILED; manual intervention needed"
    return 1
}

# ---- manual rollback -----------------------------------------------------------
if [[ "$ROLLBACK_SET" -eq 1 ]]; then
    [[ "$DRY" -eq 1 ]] || preflight_sync
    resolve_image
    if [[ "$DRY" -eq 0 ]]; then
        docker image inspect "$REPO:$ROLLBACK_TAG" >/dev/null 2>&1 || die "no such image: $REPO:$ROLLBACK_TAG"
    fi
    if restore_and_verify "$REPO:$ROLLBACK_TAG"; then note "ROLLBACK: OK"; exit 0; fi
    exit 3
fi

# ---- deploy --------------------------------------------------------------------
preflight
resolve_image
ROLLBACK_REF="$REPO:rollback-$STAMP"
START="$(now_iso)"

note "backup -> $BACKUP_DIR (state, env mode 0600, compose); env contents are never printed"
run mkdir -p -m 700 -- "$BACKUP_ROOT"
run mkdir -m 700 -- "$BACKUP_DIR"
run cp -a -- "$STATE_DIR" "$BACKUP_DIR/state"
run install -m 600 -- "$ENV_FILE" "$BACKUP_DIR/env"
run install -m 600 -- "$COMPOSE_FILE" "$BACKUP_DIR/docker-compose.yml"
run docker tag "$IMAGE_ID" "$ROLLBACK_REF"

deploy_ok=1
dc pull || deploy_ok=0
if [[ "$deploy_ok" -eq 1 ]]; then dc up -d || deploy_ok=0; fi
if [[ "$deploy_ok" -eq 1 ]]; then
    wait_ready
    run_gates "$START" "$SMOKE" || deploy_ok=0
else
    note "  FAIL docker compose pull/up"
fi

if [[ "$DRY" -eq 1 ]]; then
    note "[dry-run] on any failed step or gate the script would instead run:"
    restore_and_verify "$ROLLBACK_REF" || true
    exit 0
fi

if [[ "$deploy_ok" -eq 1 ]]; then
    note "DEPLOY: OK (rollback tag: $ROLLBACK_REF; backup: $BACKUP_DIR)"
    exit 0
fi

note "DEPLOY: a step or gate failed"
if restore_and_verify "$ROLLBACK_REF"; then
    note "DEPLOY: FAILED, rolled back to $ROLLBACK_REF"
    exit 1
fi
exit 3
