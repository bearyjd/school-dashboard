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
#   KNOWN_WATCHDOG_PROBLEMS=source:issue,...  additionally allowed watchdog problems. Problems already present in the
#     running container before the pull (the baseline) never fail the gate; only new ones do.
#   SMOKE_SCRIPT=<dir of this script>/smoke-live.sh
# Exit: 0 ok | 1 a step or gate failed, rolled back and verified
#       2 refused, bad arguments, or a pre-pull step failed (nothing was changed)
#       3 the rollback itself failed or could not be verified (manual intervention needed)
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
SQLITE3="${SQLITE3:-sqlite3}"

SLUG="deploy"; SMOKE=0; DRY=0; ROLLBACK_TAG=""; ROLLBACK_SET=0

# A closed terminal or pipe must never kill a deploy half way (set -e + EPIPE): ignore SIGPIPE and
# make all output best-effort. Run long deploys in tmux or `systemd-run` anyway (see the runbook).
trap '' PIPE

die() { echo "deploy.sh: $*" >&2 || true; exit 2; }

# The deploy lock (fd 9) must not leak into docker, curl or the smoke script.
docker() { command docker "$@" 9>&-; }
# shellcheck disable=SC2329  # used through the CURL array
curl() { command curl "$@" 9>&-; }

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
[[ "$SQLITE3" =~ ^[A-Za-z0-9._/-]+$ ]] || die "invalid SQLITE3"
BASE_URL="${BASE_URL%/}"

STATE_DIR="$DEPLOY_DIR/state"
ENV_FILE="$DEPLOY_DIR/config/env"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$BACKUP_ROOT/pre-$SLUG-$STAMP"
TMP=""
PHASE="pre"       # pre = nothing changed yet; post = pull/up started (an interrupt must roll back)
ROLLING=0
REPO=""; IMAGE_ID=""; ROLLBACK_REF=""
CURL=(curl -s --noproxy '*')

if [[ "$DRY" -eq 0 ]]; then
    TMP="$(mktemp -d)"
fi

# ---- helpers -------------------------------------------------------------------
run() {  # print in dry-run, execute otherwise
    if [[ "$DRY" -eq 1 ]]; then
        printf '[dry-run]'; printf ' %q' "$@"; printf '\n'
    else
        "$@" 9>&-
    fi
}
note() { printf '%s\n' "$*" || true; }
dc() { run docker compose -f "$COMPOSE_FILE" --project-directory "$DEPLOY_DIR" "$@"; }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# shellcheck disable=SC2329  # invoked via trap
on_exit() { if [[ -n "$TMP" ]]; then rm -rf -- "$TMP"; fi; }
trap on_exit EXIT

# shellcheck disable=SC2329  # invoked via trap
on_signal() {
    trap '' INT TERM HUP PIPE
    set +e
    # After SIGHUP (ssh drop) stdout/stderr are dead: keep the record in a private log instead.
    if [[ "$DRY" -eq 0 && -d "$BACKUP_ROOT" ]]; then exec >>"$BACKUP_ROOT/deploy-$STAMP.log" 2>&1; fi
    if [[ "$ROLLING" -eq 1 ]]; then
        note "INTERRUPTED during a rollback: manual intervention needed, check the container and the image tags"
        exit 3
    fi
    if [[ "$PHASE" == "post" ]]; then
        note "INTERRUPTED after the pull started: rolling back"
        if restore_and_verify "$ROLLBACK_REF" "$IMAGE_ID"; then exit 1; fi
        exit 3
    fi
    note "deploy.sh: interrupted before anything was changed"
    exit 2
}
trap on_signal INT TERM HUP

preflight_sync() {
    shopt -s nullglob
    local markers=("$STATE_DIR"/sync.running.*)
    shopt -u nullglob
    [[ ${#markers[@]} -eq 0 ]] || die "a sync is running (state/sync.running.* present); wait for it"
}

preflight_quiet() {  # sync + maintenance; run again right before the pull
    preflight_sync
    [[ ! -e "$STATE_DIR/maintenance.json" ]] || die "maintenance mode is on; turn it off first"
}

check_free() {  # path label
    local avail_kb
    avail_kb="$(df -Pk -- "$1" | awk 'NR==2 {print $4}')"
    [[ "$avail_kb" =~ ^[0-9]+$ ]] || die "cannot read free disk space for $2"
    (( avail_kb >= MIN_FREE_GB * 1024 * 1024 )) || die "less than ${MIN_FREE_GB}G free for $2 ($1)"
}

check_compose_image_tag() {
    local line v last
    while IFS= read -r line; do
        v="${line#*image:}"; v="${v//[[:space:]\"\']/}"
        [[ "$v" != *'$'* && "$v" != *'@'* ]] || die "compose image '$v' cannot be checked; rollback needs a plain ...:latest image"
        last="${v##*/}"
        if [[ "$last" == *:* && "${last##*:}" != "latest" ]]; then
            die "compose image tag is not :latest ($v); the rollback retags :latest, so it would not take effect"
        fi
    done < <(grep -E '^[[:space:]]*image:' -- "$COMPOSE_FILE" || true)
}

preflight() {
    if [[ "$DRY" -eq 1 ]]; then
        note "[dry-run] preflight: no $STATE_DIR/sync.running.*, no $STATE_DIR/maintenance.json, env and compose files exist,"
        note "[dry-run]            compose has no /app/web mount and only :latest images,"
        note "[dry-run]            >= ${MIN_FREE_GB}G free on $DEPLOY_DIR, the Docker root dir (docker info) and $BACKUP_ROOT"
        return
    fi
    preflight_quiet
    [[ -f "$ENV_FILE" && -f "$COMPOSE_FILE" ]] || die "missing $ENV_FILE or $COMPOSE_FILE"
    if grep -q '/app/web' "$COMPOSE_FILE"; then die "compose file mounts /app/web; it would shadow the image (see runbook)"; fi
    check_compose_image_tag
    check_free "$DEPLOY_DIR" "the deploy dir"
    local droot bdir
    droot="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)" || die "cannot read the Docker root dir"
    [[ "$droot" == /* && "$droot" =~ ^[A-Za-z0-9._/-]+$ ]] || die "unexpected Docker root dir"
    check_free "$droot" "the Docker root dir"
    bdir="$BACKUP_ROOT"
    while [[ ! -d "$bdir" && "$bdir" != "/" ]]; do bdir="$(dirname -- "$bdir")"; done
    check_free "$bdir" "the backup dir"
}

take_lock() {
    if [[ "$DRY" -eq 1 ]]; then note "[dry-run] mkdir -p -m 700 $BACKUP_ROOT; flock -n $BACKUP_ROOT/.deploy.lock"; return; fi
    command -v flock >/dev/null || die "flock is required"
    { mkdir -p -- "$BACKUP_ROOT" && chmod 700 -- "$BACKUP_ROOT"; } || die "cannot create $BACKUP_ROOT"
    exec 9>"$BACKUP_ROOT/.deploy.lock" || die "cannot open the lock file"
    flock -n 9 || die "another deploy is running"
}

wait_ready() {
    [[ "$DRY" -eq 0 ]] || { note "[dry-run] wait up to ${READY_TIMEOUT}s for GET /api/sync/status = 200"; return 0; }
    local waited=0 code
    while :; do
        code="$("${CURL[@]}" -o /dev/null -w '%{http_code}' --max-time 20 "$BASE_URL/api/sync/status" 2>/dev/null || true)"
        [[ "$code" == "200" || "$waited" -ge "$READY_TIMEOUT" ]] && return 0
        sleep 2; waited=$((waited + 2))
    done
}

FAILS=0
gate() {  # name, PASS|FAIL|SKIP
    printf '  %-4s %s\n' "$2" "$1" || true
    [[ "$2" != FAIL ]] || FAILS=$((FAILS + 1))
}

http_code() { "${CURL[@]}" -o /dev/null -w '%{http_code}' --max-time 20 "$BASE_URL$1" 2>/dev/null || true; }
cache_name() { grep -o "CACHE = '[^']*'" -- "$1" | head -n1 || true; }

watchdog_snapshot() {  # $1 = output file
    docker exec -- "$CONTAINER" school-state watchdog --dry-run --json > "$1" 2>/dev/null || true
}

# prints the number of watchdog problems in $2 that are in neither the baseline $1 nor KNOWN_WATCHDOG_PROBLEMS
# (a missing or unreadable baseline counts as empty; an unreadable current result prints "bad")
count_new_problems() {
    python3 -I - "$1" "$2" "$KNOWN_WATCHDOG_PROBLEMS" <<'PY' || echo bad
import json, sys

def keys(path):
    data = json.load(open(path))
    return {f'{p["source"]}:{p["issue"]}' for p in data["problems"]}

try:
    try:
        base = keys(sys.argv[1])
    except Exception:
        base = set()
    known = {k for k in sys.argv[3].split(",") if k}
    print(len(keys(sys.argv[2]) - base - known))
except Exception:
    print("bad")
PY
}

run_gates() {  # sets FAILS; $1 = ISO time the (re)start began; $2 = smoke (0|1); $3 = watchdog gate (1) or skip (0)
    local since="$1" with_smoke="$2" with_wd="$3" p code out served built n rc
    FAILS=0
    if [[ "$DRY" -eq 1 ]]; then
        note "[dry-run] gates: GET /api/dashboard /api/overview /api/readiness /api/sync/status /api/sync/meta /app/ /legacy = 200;"
        note "[dry-run]        GET / = 302 -> /app/; served /app/sw.js cache name == built dist/sw.js in $CONTAINER;"
        note "[dry-run]        docker exec $CONTAINER school-state transition maintenance check = exit 10;"
        note "[dry-run]        docker logs --since <start> $CONTAINER readable and 0 tracebacks;"
        [[ "$with_wd" -eq 0 ]] || note "[dry-run]        watchdog --dry-run --json: no problem beyond the pre-deploy baseline + KNOWN_WATCHDOG_PROBLEMS"
        [[ "$with_smoke" -eq 0 ]] || note "[dry-run]        $SMOKE_SCRIPT --container $CONTAINER"
        return 0
    fi
    for p in /api/dashboard /api/overview /api/readiness /api/sync/status /api/sync/meta /app/ /legacy; do
        code="$(http_code "$p")"
        if [[ "$code" == "200" ]]; then gate "GET $p" PASS; else gate "GET $p (HTTP ${code:-000})" FAIL; fi
    done
    out="$("${CURL[@]}" -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 20 "$BASE_URL/" 2>/dev/null || true)"
    if [[ "${out%% *}" == "302" && "${out#* }" == */app/ ]]; then gate "GET / -> 302 /app/" PASS; else gate "GET / -> 302 /app/ (got ${out%% *})" FAIL; fi

    "${CURL[@]}" -o "$TMP/sw.served" --max-time 20 "$BASE_URL/app/sw.js" 2>/dev/null || true
    docker exec -- "$CONTAINER" cat /app/web/spa/dist/sw.js > "$TMP/sw.built" 2>/dev/null || true
    served="$(cache_name "$TMP/sw.served")"; built="$(cache_name "$TMP/sw.built")"
    if [[ -n "$served" && "$served" == "$built" ]]; then gate "service worker cache name matches build" PASS; else gate "service worker cache name matches build" FAIL; fi

    rc=0; docker exec -- "$CONTAINER" school-state transition maintenance check >/dev/null 2>&1 || rc=$?
    if [[ "$rc" -eq 10 ]]; then gate "maintenance gate exit 10" PASS; else gate "maintenance gate (exit $rc, expected 10)" FAIL; fi

    if out="$(docker logs --since "$since" "$CONTAINER" 2>&1)"; then
        n="$(grep -c 'Traceback' <<< "$out" || true)"
        if [[ "$n" == "0" ]]; then gate "no tracebacks in container log" PASS; else gate "tracebacks in container log: $n" FAIL; fi
    else
        gate "container log unreadable" FAIL
    fi

    if [[ "$with_wd" -eq 1 ]]; then
        watchdog_snapshot "$TMP/wd.now"
        n="$(count_new_problems "$TMP/wd.base" "$TMP/wd.now")"
        if [[ "$n" == "0" ]]; then gate "watchdog: no new problems" PASS; else gate "watchdog: new problems or unreadable result ($n)" FAIL; fi
    else
        gate "watchdog (skipped during rollback verification)" SKIP
    fi

    if [[ "$with_smoke" -eq 1 ]]; then
        if BASE_URL="$BASE_URL" "$SMOKE_SCRIPT" --container "$CONTAINER" > "$TMP/smoke.out" 2>&1 9>&-; then
            gate "smoke-live" PASS
        else
            gate "smoke-live" FAIL
            tail -n 5 -- "$TMP/smoke.out" | sed 's/^/        smoke: /' || true
        fi
    fi
    [[ "$FAILS" -eq 0 ]]
}

resolve_image() {  # sets REPO and IMAGE_ID; $1 = "fallback" lets a missing container fall back to the compose file
    if [[ "$DRY" -eq 1 ]]; then REPO="IMAGE_REPO"; IMAGE_ID="RUNNING_IMAGE_ID"; return; fi
    local img last v
    if img="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER" 2>/dev/null)" \
        && IMAGE_ID="$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null)"; then
        :
    elif [[ "${1:-}" == "fallback" ]]; then
        v="$(grep -E '^[[:space:]]*image:' -- "$COMPOSE_FILE" | head -n1 || true)"
        img="${v#*image:}"; img="${img//[[:space:]\"\']/}"
        IMAGE_ID=""
        [[ -n "$img" ]] || die "container $CONTAINER not found and no image in $COMPOSE_FILE"
    else
        die "cannot inspect container $CONTAINER"
    fi
    last="${img##*/}"
    if [[ "$last" == *:* ]]; then REPO="${img%:*}"; else REPO="$img"; fi
    [[ "$REPO" =~ ^[A-Za-z0-9._/:-]+$ ]] || die "unexpected image name"
    [[ -z "$IMAGE_ID" || "$IMAGE_ID" =~ ^[A-Za-z0-9:]+$ ]] || die "unexpected image id"
}

restore_and_verify() {  # $1 = image ref to restore as :latest; $2 = image id the container must end up running
    ROLLING=1
    trap '' INT TERM HUP  # a rollback must finish; it is never half-done on purpose
    note "ROLLBACK: restoring $1 as ${REPO}:latest"
    if ! run docker tag "$1" "$REPO:latest"; then
        note "ROLLBACK FAILED: could not retag; the container was NOT recreated"
        return 1
    fi
    if ! dc up -d --force-recreate; then
        note "ROLLBACK FAILED: docker compose up --force-recreate failed"
        return 1
    fi
    local since cur; since="$(now_iso)"
    wait_ready
    if ! run_gates "$since" 0 0; then
        note "ROLLBACK: verification FAILED; manual intervention needed"
        return 1
    fi
    if [[ "$DRY" -eq 1 ]]; then
        note "[dry-run] docker inspect --format '{{.Image}}' $CONTAINER must equal the pre-deploy image id"
    else
        cur="$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || true)"
        if [[ -z "$2" || "$cur" != "$2" ]]; then
            note "ROLLBACK: verification FAILED; running image is not the rolled-back image"
            return 1
        fi
    fi
    note "ROLLBACK: verified"
    return 0
}

# ---- manual rollback -----------------------------------------------------------
if [[ "$ROLLBACK_SET" -eq 1 ]]; then
    if [[ "$DRY" -eq 1 ]]; then
        note "[dry-run] preflight: no sync running, maintenance off"
    else
        preflight_quiet
    fi
    take_lock
    resolve_image fallback
    target_id="RUNNING_IMAGE_ID"
    if [[ "$DRY" -eq 0 ]]; then
        target_id="$(docker image inspect --format '{{.Id}}' "$REPO:$ROLLBACK_TAG" 2>/dev/null)" \
            || die "no such image: $REPO:$ROLLBACK_TAG"
        [[ "$target_id" =~ ^[A-Za-z0-9:]+$ ]] || die "unexpected image id"
    fi
    # keep what :latest points at now, so the rollback itself can be undone
    if [[ "$DRY" -eq 1 ]] || docker image inspect "$REPO:latest" >/dev/null 2>&1; then
        run docker tag "$REPO:latest" "$REPO:rollback-pre-$STAMP" || die "cannot tag the current :latest as rollback-pre-$STAMP"
    fi
    PHASE="post"
    if restore_and_verify "$REPO:$ROLLBACK_TAG" "$target_id"; then note "ROLLBACK: OK"; exit 0; fi
    exit 3
fi

# ---- deploy --------------------------------------------------------------------
preflight
take_lock
resolve_image
ROLLBACK_REF="$REPO:rollback-$STAMP"

if [[ "$DRY" -eq 1 ]]; then
    note "[dry-run] baseline: docker exec $CONTAINER school-state watchdog --dry-run --json (before the pull)"
else
    watchdog_snapshot "$TMP/wd.base"
fi

note "backup -> $BACKUP_DIR (state, env mode 0600, compose); env contents are never printed"
run mkdir -m 700 -- "$BACKUP_DIR" || die "cannot create the backup dir"
run mkdir -m 700 -- "$BACKUP_DIR/state" || die "backup failed (state dir)"
run cp -a -- "$STATE_DIR/." "$BACKUP_DIR/state/" || die "backup failed (state copy)"
if [[ "$DRY" -eq 1 ]]; then
    note "[dry-run] if $STATE_DIR/school.db exists: sqlite3 .backup into the backup, PRAGMA integrity_check must say ok (else exit 2 before the pull)"
elif [[ -f "$STATE_DIR/school.db" ]]; then
    if command -v "$SQLITE3" >/dev/null; then
        # a plain cp of a live database may be torn: replace it with a consistent, verified copy
        rm -f -- "$BACKUP_DIR/state/school.db" "$BACKUP_DIR/state/school.db-wal" "$BACKUP_DIR/state/school.db-shm"
        "$SQLITE3" "$STATE_DIR/school.db" ".backup '$BACKUP_DIR/state/school.db.tmp'" 9>&- || die "backup failed (sqlite backup)"
        check="$("$SQLITE3" "$BACKUP_DIR/state/school.db.tmp" "PRAGMA integrity_check" 9>&-)" || die "backup failed (sqlite integrity check could not run)"
        [[ "$check" == "ok" ]] || die "backup failed (sqlite integrity check on the copy)"
        mv -- "$BACKUP_DIR/state/school.db.tmp" "$BACKUP_DIR/state/school.db" || die "backup failed (sqlite)"
    else
        note "WARN: $SQLITE3 not found; school.db in the backup is a plain copy of a possibly live file (with its -wal/-shm)"
    fi
fi
run install -m 600 -- "$ENV_FILE" "$BACKUP_DIR/env" || die "backup failed (env)"
run install -m 600 -- "$COMPOSE_FILE" "$BACKUP_DIR/docker-compose.yml" || die "backup failed (compose)"
run docker tag "$IMAGE_ID" "$ROLLBACK_REF" || die "cannot tag the running image as $ROLLBACK_REF"
run touch -- "$BACKUP_DIR/COMPLETE" || die "backup failed (marker)"  # last: a dir without it is an interrupted backup

if [[ "$DRY" -eq 1 ]]; then
    note "[dry-run] re-check: no sync running, maintenance off (immediately before the pull)"
else
    preflight_quiet
fi

PHASE="post"
deploy_ok=1
dc pull || deploy_ok=0
START="$(now_iso)"
if [[ "$deploy_ok" -eq 1 ]]; then dc up -d || deploy_ok=0; fi
if [[ "$deploy_ok" -eq 1 ]]; then
    wait_ready
    run_gates "$START" "$SMOKE" 1 || deploy_ok=0
else
    note "  FAIL docker compose pull/up"
fi

if [[ "$DRY" -eq 1 ]]; then
    note "[dry-run] on any failed step or gate the script would instead run:"
    restore_and_verify "$ROLLBACK_REF" "$IMAGE_ID" || true
    exit 0
fi

if [[ "$deploy_ok" -eq 1 ]]; then
    PHASE="done"
    note "DEPLOY: OK (rollback tag: $ROLLBACK_REF; backup: $BACKUP_DIR)"
    exit 0
fi

note "DEPLOY: a step or gate failed"
if restore_and_verify "$ROLLBACK_REF" "$IMAGE_ID"; then
    note "DEPLOY: FAILED, rolled back to $ROLLBACK_REF"
    exit 1
fi
exit 3
