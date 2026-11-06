#!/usr/bin/env bash
# gc-refresh.sh — run `gc token-refresh` under the shared host/container lock.
# The host keep-alive timer (docs/ops/gc-keepalive.md) takes the same lock, so a
# sync and the timer never run two GameChanger logins at once. Non-fatal: always
# exits 0; the CLI's auto-retry on 401 is the safety net.
#
# Env: GC_REFRESH_LOCK (default /root/.gc/refresh.lock),
#      GC_REFRESH_LOCK_WAIT seconds (default 600).
set -uo pipefail

LOCK="${GC_REFRESH_LOCK:-/root/.gc/refresh.lock}"
WAIT="${GC_REFRESH_LOCK_WAIT:-600}"
# flock -E code for "lock wait timed out"; 97 is not an exit code gc uses.
LOCK_BUSY=97

log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }

if command -v flock &>/dev/null; then
    # -o: close the lock fd before exec so gc's children (Chromium) can't keep it held.
    flock -o -w "$WAIT" -E "$LOCK_BUSY" "$LOCK" gc token-refresh 2>&1 | sed 's/^/  /' >&2
    rc=${PIPESTATUS[0]}
    if [[ $rc -eq $LOCK_BUSY ]]; then
        log "WARN: gc token-refresh lock ($LOCK) busy after ${WAIT}s — skipping refresh (continuing)"
    elif [[ $rc -ne 0 ]]; then
        log "WARN: gc token-refresh exited non-zero (continuing — auto-retry on 401 is the safety net)"
    fi
else
    log "WARN: flock not found — running gc token-refresh without the shared lock"
    gc token-refresh 2>&1 | sed 's/^/  /' >&2 \
        || log "WARN: gc token-refresh exited non-zero (continuing — auto-retry on 401 is the safety net)"
fi
exit 0
