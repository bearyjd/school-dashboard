#!/usr/bin/env bash
# Regenerate requirements.lock from a clean venv: the app's [server] extra plus
# the vendored scrapers (same installs as the Dockerfile), then pip freeze.
# Usage: scripts/update-lock.sh [--upgrade]   (needs the vendor/ submodules checked out)
# Without --upgrade the current pins are used as constraints, so only missing
# packages are resolved. With --upgrade everything floats to the newest allowed version.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
for d in ixl-scrape schoology-scrape gc powerschool-scrape; do
    [ -f "vendor/$d/pyproject.toml" ] || { echo "vendor/$d missing: run git submodule update --init" >&2; exit 1; }
done

# The image runs Python 3.15; resolving on another minor could pick different pins.
PY="$(command -v python3.15 || true)"
if [ -z "$PY" ]; then
    case "$(python3 --version 2>&1)" in
        "Python 3.15."*) PY="$(command -v python3)" ;;
        *) echo "need python3.15 (the Dockerfile base); found: $(python3 --version 2>&1)" >&2; exit 1 ;;
    esac
fi

# Plain string, not an array: empty-array expansion fails under set -u on bash < 4.4.
CONSTRAIN="-c requirements.lock"
[ "${1:-}" = "--upgrade" ] && CONSTRAIN=""

TMP="$(mktemp -d)"
cleanup() {
    rm -rf "$TMP" build ./*.egg-info vendor/*/build vendor/*/*.egg-info
}
trap cleanup EXIT
"$PY" -m venv "$TMP/venv"
"$TMP/venv/bin/pip" install --quiet --upgrade pip
"$TMP/venv/bin/pip" install --quiet $CONSTRAIN ".[server]" \
    -e "vendor/ixl-scrape[browser]" \
    -e vendor/schoology-scrape \
    -e vendor/gc \
    -e "vendor/powerschool-scrape[browser]"

{
    cat <<'HDR'
# Pinned production dependency set (pip constraints file). Plain name==version pins, no hashes:
# hashes would force --require-hashes on every install, which breaks the editable vendor installs.
# Used by the Dockerfile: pip install -c requirements.lock ...
# Excludes the local projects (school-dashboard and the vendor/ scrapers) and pip/setuptools/wheel.
# Regenerate with scripts/update-lock.sh [--upgrade] (clean venv, same installs as the Dockerfile,
# pip freeze, filtered). Renovate also bumps these pins.
HDR
    "$TMP/venv/bin/pip" freeze --exclude-editable \
        | grep -v -i -E '^(school-dashboard|pip|setuptools|wheel)==' \
        | LC_ALL=C sort -f
} > requirements.lock
echo "wrote requirements.lock"
