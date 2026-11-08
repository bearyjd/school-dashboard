"""Tests for scripts/deploy.sh using fake docker/curl/df/ssh shims on PATH (no real host, no network)."""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"
SECRET = "SENTINEL_SECRET_VALUE_123"
IMG_REPO = "ghcr.io/example/school-dashboard"

# The shims model "new image is up" with a marker file: `compose up -d` creates it, a
# `--force-recreate` (the rollback) removes it. FAKE_FAIL only misbehaves while the marker exists,
# except STICKY=1 (the force-recreate keeps the marker, so the rollback cannot recover).
DOCKER_SHIM = r"""#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$SHIM_LOG"
NEW="$SHIM_STATE/new"
case "$1" in
  inspect)
    case "$*" in
      *'{{.Config.Image}}'*) echo "ghcr.io/example/school-dashboard:latest" ;;
      *'{{.Image}}'*) echo "sha256:0123456789abcdef" ;;
    esac ;;
  image) [[ -z "${FAKE_NO_IMAGE:-}" ]] || exit 1 ;;
  tag) ;;
  compose)
    case "$*" in
      *" pull"*) [[ "${FAKE_FAIL:-}" != pull ]] || exit 1 ;;
      *"--force-recreate"*) [[ -n "${STICKY:-}" ]] || rm -f "$NEW" ;;
      *" up -d"*) [[ "${FAKE_FAIL:-}" != up ]] || exit 1; touch "$NEW" ;;
    esac ;;
  exec)
    case "$*" in
      *"maintenance check"*)
        if [[ -e "$NEW" && "${FAKE_FAIL:-}" == maint ]]; then exit 0; fi
        exit 10 ;;
      *"watchdog --dry-run"*)
        if [[ -e "$NEW" && "${FAKE_FAIL:-}" == watchdog ]]; then
          echo '{"problems":[{"source":"gc","issue":"failing","age_hours":30}]}'; exit 1
        fi
        echo '{"problems":[{"source":"ixl","issue":"stale","age_hours":40}]}'; exit 1 ;;
      *"sw.js"*) echo "const CACHE = 'school-v3';" ;;
    esac ;;
  logs)
    echo "INFO started"
    if [[ -e "$NEW" && "${FAKE_FAIL:-}" == traceback ]]; then echo "Traceback (most recent call last):"; fi ;;
esac
exit 0
"""

CURL_SHIM = r"""#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$SHIM_LOG"
out=""; fmt=""; url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w) fmt="$2"; shift 2 ;;
    --max-time) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
rest="${url#*://}"; path="/${rest#*/}"
code=200; redir=""; body="{}"
if [[ "$path" == "/" ]]; then code=302; redir="http://host/app/"; fi
if [[ "$path" == "/app/sw.js" ]]; then body="const CACHE = 'school-v3';"; fi
if [[ -e "$SHIM_STATE/new" ]]; then
  case "${FAKE_FAIL:-}" in
    route:*) [[ "$path" != "${FAKE_FAIL#route:}" ]] || code=500 ;;
    root) [[ "$path" != "/" ]] || { code=200; redir=""; } ;;
    sw) [[ "$path" != "/app/sw.js" ]] || body="const CACHE = 'school-v2';" ;;
  esac
fi
[[ -z "$out" || "$out" == /dev/null ]] || printf '%s' "$body" > "$out"
fmt="${fmt//%\{http_code\}/$code}"; fmt="${fmt//%\{redirect_url\}/$redir}"
printf '%s' "$fmt"
exit 0
"""

DF_SHIM = r"""#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted\nfake 999999999 1 %s 1%% /\n' "${FAKE_DF_AVAIL_KB:-104857600}"
"""

SSH_SHIM = '#!/usr/bin/env bash\nprintf \'ssh %s\\n\' "$*" >> "$SHIM_LOG"\nexit 0\n'

SMOKE_OK = "#!/usr/bin/env bash\nexit 0\n"
SMOKE_BAD = "#!/usr/bin/env bash\nexit 1\n"


def _write(path: Path, text: str, mode: int = 0o755) -> None:
    path.write_text(text)
    path.chmod(mode)


@pytest.fixture
def env(tmp_path):
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in (("docker", DOCKER_SHIM), ("curl", CURL_SHIM), ("df", DF_SHIM), ("ssh", SSH_SHIM)):
        _write(shims / name, body)
    deploy = tmp_path / "opt-school"
    (deploy / "state").mkdir(parents=True)
    (deploy / "state" / "school-state.json").write_text("{}")
    (deploy / "config").mkdir()
    _write(deploy / "config" / "env", f"SYNC_TOKEN={SECRET}\n", 0o600)
    (deploy / "docker-compose.yml").write_text("services:\n  dashboard:\n    image: x\n")
    smoke = tmp_path / "smoke.sh"
    _write(smoke, SMOKE_OK)
    shim_state = tmp_path / "shim-state"
    shim_state.mkdir()
    return {
        "PATH": f"{shims}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "DEPLOY_DIR": str(deploy),
        "BACKUP_ROOT": str(tmp_path / "backups"),
        "BASE_URL": "http://127.0.0.1:5000",
        "READY_TIMEOUT": "0",
        "SMOKE_SCRIPT": str(smoke),
        "SHIM_LOG": str(tmp_path / "shim.log"),
        "SHIM_STATE": str(shim_state),
    }


def run(env, *args, **extra):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                          env={**env, **extra}, timeout=60, cwd=env["TMPDIR"])


def log(env):
    p = Path(env["SHIM_LOG"])
    return p.read_text().splitlines() if p.exists() else []


def backups(env):
    root = Path(env["BACKUP_ROOT"])
    return sorted(root.iterdir()) if root.exists() else []


def assert_no_secret(p):
    assert SECRET not in p.stdout and SECRET not in p.stderr


# --- dry run -----------------------------------------------------------------

def test_dry_run_touches_nothing_and_prints_commands(env):
    p = run(env, "--dry-run", "--slug", "spa")
    assert p.returncode == 0, p.stderr
    assert log(env) == []
    assert not Path(env["BACKUP_ROOT"]).exists()
    for needle in ("docker compose", "pull", "up -d", "docker tag", "rollback-", "--force-recreate", "pre-spa-", "[dry-run]"):
        assert needle in p.stdout
    assert_no_secret(p)


def test_dry_run_rollback_prints_commands(env):
    p = run(env, "--dry-run", "--rollback", "rollback-20260101T000000Z")
    assert p.returncode == 0, p.stderr
    assert log(env) == []
    assert "rollback-20260101T000000Z" in p.stdout and ":latest" in p.stdout


# --- happy path --------------------------------------------------------------

def test_happy_path(env):
    p = run(env, "--slug", "spa", "--smoke", KNOWN_WATCHDOG_PROBLEMS="ixl:stale")
    assert p.returncode == 0, p.stdout + p.stderr
    calls = log(env)
    assert not any(c.startswith("ssh") for c in calls)
    tags = [c for c in calls if c.startswith("docker tag ")]
    assert len(tags) == 1
    assert re.fullmatch(rf"docker tag sha256:0123456789abcdef {re.escape(IMG_REPO)}:rollback-\d{{8}}T\d{{6}}Z", tags[0])
    pull = next(i for i, c in enumerate(calls) if " pull" in c)
    up = next(i for i, c in enumerate(calls) if " up -d" in c)
    assert calls.index(tags[0]) < pull < up
    assert not any("--force-recreate" in c for c in calls)
    (b,) = backups(env)
    assert re.fullmatch(r"pre-spa-\d{8}T\d{6}Z", b.name)
    assert stat.S_IMODE((b / "env").stat().st_mode) == 0o600
    assert (b / "env").read_text() == f"SYNC_TOKEN={SECRET}\n"
    assert (b / "state" / "school-state.json").exists()
    assert (b / "docker-compose.yml").exists()
    assert "DEPLOY: OK" in p.stdout
    assert_no_secret(p)


def test_known_watchdog_problems(env):
    # the shim always reports ixl:stale; unlisted, it fails the gate (and the rollback re-check) ...
    assert run(env, "--slug", "spa").returncode == 3
    # ... and passes once listed as known
    assert run(env, "--slug", "spa2", KNOWN_WATCHDOG_PROBLEMS="ixl:stale").returncode == 0
    # a new problem is still caught
    assert run(env, "--slug", "spa3", KNOWN_WATCHDOG_PROBLEMS="ixl:stale", FAKE_FAIL="watchdog").returncode == 1


# --- gate failures -> rollback ------------------------------------------------

GATE_FAILS = [
    "pull", "up",
    "route:/api/dashboard", "route:/api/overview", "route:/api/readiness",
    "route:/api/sync/status", "route:/api/sync/meta", "route:/app/", "route:/legacy",
    "root", "sw", "maint", "traceback", "watchdog",
]


@pytest.mark.parametrize("fail", GATE_FAILS)
def test_gate_failure_rolls_back(env, fail):
    p = run(env, "--slug", "spa", FAKE_FAIL=fail, KNOWN_WATCHDOG_PROBLEMS="ixl:stale")
    assert p.returncode == 1, p.stdout + p.stderr
    calls = log(env)
    tag = re.search(r"docker tag \S+ (\S+:rollback-\S+)", "\n".join(calls)).group(1)
    retag = f"docker tag {tag} {IMG_REPO}:latest"
    assert retag in calls
    recreate = next(i for i, c in enumerate(calls) if "up -d --force-recreate" in c)
    assert calls.index(retag) < recreate
    assert any(c.startswith("curl") for c in calls[recreate:]), "rollback must be re-verified"
    assert "ROLLBACK" in p.stdout
    assert_no_secret(p)


def test_smoke_failure_rolls_back(env):
    _write(Path(env["SMOKE_SCRIPT"]), SMOKE_BAD)
    p = run(env, "--smoke", KNOWN_WATCHDOG_PROBLEMS="ixl:stale")
    assert p.returncode == 1
    assert any("up -d --force-recreate" in c for c in log(env))


def test_no_smoke_unless_requested(env):
    _write(Path(env["SMOKE_SCRIPT"]), SMOKE_BAD)
    assert run(env, KNOWN_WATCHDOG_PROBLEMS="ixl:stale").returncode == 0


def test_failed_rollback_verification_exits_3(env):
    p = run(env, FAKE_FAIL="route:/app/", STICKY="1", KNOWN_WATCHDOG_PROBLEMS="ixl:stale")
    assert p.returncode == 3, p.stdout + p.stderr


# --- refusals -----------------------------------------------------------------

def _nothing_changed(env):
    assert not any(c.startswith("docker compose") or c.startswith("docker tag") for c in log(env))
    assert backups(env) == []


def test_refuses_during_sync(env):
    (Path(env["DEPLOY_DIR"]) / "state" / "sync.running.1234").write_text("")
    p = run(env)
    assert p.returncode == 2 and "sync" in p.stderr.lower()
    _nothing_changed(env)


def test_refuses_during_maintenance(env):
    (Path(env["DEPLOY_DIR"]) / "state" / "maintenance.json").write_text("{}")
    p = run(env)
    assert p.returncode == 2 and "maintenance" in p.stderr.lower()
    _nothing_changed(env)


def test_refuses_low_disk(env):
    p = run(env, FAKE_DF_AVAIL_KB=str(10 * 1024 * 1024))
    assert p.returncode == 2 and "free" in p.stderr.lower()
    _nothing_changed(env)


def test_refuses_web_mount_in_compose(env):
    (Path(env["DEPLOY_DIR"]) / "docker-compose.yml").write_text("    volumes:\n      - ./web:/app/web\n")
    p = run(env)
    assert p.returncode == 2 and "/app/web" in p.stderr
    _nothing_changed(env)


# --- argument validation ------------------------------------------------------

BAD = ["a;b", "$(touch PWNED)", "../x", "a b", "a/b", "`id`", "x\nid", "", "a'b"]


@pytest.mark.parametrize("bad", BAD)
def test_rejects_bad_slug(env, bad):
    p = run(env, "--slug", bad)
    assert p.returncode == 2
    assert log(env) == [] and backups(env) == []
    assert not (Path(env["TMPDIR"]) / "PWNED").exists()


@pytest.mark.parametrize("bad", BAD)
def test_rejects_bad_rollback_tag(env, bad):
    p = run(env, "--rollback", bad)
    assert p.returncode == 2
    assert log(env) == []


def test_rejects_bad_slug_in_dry_run_too(env):
    assert run(env, "--dry-run", "--slug", "a;b").returncode == 2


def test_unknown_argument_and_missing_value(env):
    assert run(env, "--nope").returncode == 2
    assert run(env, "--slug").returncode == 2
    assert run(env, "--rollback").returncode == 2


def test_rejects_bad_env_values(env):
    assert run(env, CONTAINER="a;b").returncode == 2
    assert run(env, BASE_URL="file:///etc/passwd").returncode == 2
    assert run(env, MIN_FREE_GB="lots").returncode == 2
    assert run(env, DEPLOY_DIR="relative/dir").returncode == 2
    assert log(env) == []


# --- manual rollback ----------------------------------------------------------

def test_rollback_path(env):
    p = run(env, "--rollback", "rollback-20260101T000000Z", KNOWN_WATCHDOG_PROBLEMS="ixl:stale")
    assert p.returncode == 0, p.stdout + p.stderr
    calls = log(env)
    assert f"docker tag {IMG_REPO}:rollback-20260101T000000Z {IMG_REPO}:latest" in calls
    assert any("up -d --force-recreate" in c for c in calls)
    assert not any(" pull" in c for c in calls)
    assert backups(env) == []
    assert_no_secret(p)


def test_rollback_missing_image(env):
    p = run(env, "--rollback", "nope", FAKE_NO_IMAGE="1")
    assert p.returncode == 2
    assert not any(c.startswith("docker tag") for c in log(env))


def test_rollback_refuses_during_sync(env):
    (Path(env["DEPLOY_DIR"]) / "state" / "sync.running.9").write_text("")
    assert run(env, "--rollback", "nope").returncode == 2


def test_rollback_failed_verification_exits_3(env):
    (Path(env["SHIM_STATE"]) / "new").write_text("")
    p = run(env, "--rollback", "t1", FAKE_FAIL="route:/app/", STICKY="1", KNOWN_WATCHDOG_PROBLEMS="ixl:stale")
    assert p.returncode == 3


# --- live-check.mjs -----------------------------------------------------------

def test_live_check_rejects_bad_base_url():
    node = subprocess.run(["bash", "-c", "command -v node"], capture_output=True, text=True)
    if node.returncode != 0:
        pytest.skip("node not installed")
    live = Path(SCRIPT).parent / "live-check.mjs"
    p = subprocess.run(["node", str(live), "http://a;b"], capture_output=True, text=True)
    assert p.returncode == 2
