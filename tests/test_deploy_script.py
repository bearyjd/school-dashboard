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
    [[ "${FAKE_FAIL:-}" != inspect ]] || exit 1
    [[ "${FAKE_FAIL:-}" != nocontainer || -e "$SHIM_STATE/recreated" ]] || exit 1
    case "$*" in
      *'{{.Config.Image}}'*) echo "ghcr.io/example/school-dashboard:latest" ;;
      *'{{.Image}}'*) if [[ -e "$SHIM_STATE/img_new" ]]; then echo "sha256:fedcba9876543210"; else echo "sha256:0123456789abcdef"; fi ;;
    esac ;;
  image)
    [[ -z "${FAKE_NO_IMAGE:-}" ]] || exit 1
    case "$*" in *"{{.Id}}"*) echo "sha256:0123456789abcdef" ;; esac ;;
  info) echo "${FAKE_DOCKER_ROOT:-/var/lib/docker}" ;;
  tag)
    [[ "${FAKE_FAIL:-}" != tag || "$3" != *:rollback-* ]] || exit 1
    [[ -z "${FAKE_TAG_RESTORE_FAIL:-}" || "$3" != *:latest ]] || exit 1
    [[ "${FAKE_SYNC_AFTER_TAG:-}" != 1 ]] || touch "$DEPLOY_DIR/state/sync.running.77"
    [[ "${FAKE_HANG:-}" != tag || "$3" != *:rollback-* ]] || sleep 2 ;;
  compose)
    case "$*" in
      *" pull"*) compgen -G "$BACKUP_ROOT/pre-*/env" >/dev/null && echo SAW_BACKUP >> "$SHIM_LOG"
        [[ "${FAKE_FAIL:-}" != pull ]] || exit 1; [[ "${FAKE_HANG:-}" != pull ]] || sleep 2 ;;
      *"--force-recreate"*) touch "$SHIM_STATE/recreated"; [[ -n "${STICKY:-}" ]] || rm -f "$NEW"; [[ -n "${IMG_STUCK:-}" ]] || rm -f "$SHIM_STATE/img_new" ;;
      *" up -d"*) [[ "${FAKE_FAIL:-}" != up ]] || exit 1; touch "$NEW" "$SHIM_STATE/img_new" ;;
    esac ;;
  exec)
    case "$*" in
      *"maintenance check"*)
        if [[ -e "$NEW" && "${FAKE_FAIL:-}" == maint ]]; then exit 0; fi
        exit 10 ;;
      *"watchdog --dry-run"*)
        if [[ -e "$NEW" && "${FAKE_FAIL:-}" == watchdog ]]; then
          echo '{"problems":[{"source":"ixl","issue":"stale","age_hours":40},{"source":"gc","issue":"failing","age_hours":30}]}'; exit 1
        fi
        echo '{"problems":[{"source":"ixl","issue":"stale","age_hours":40}]}'; exit 1 ;;
      *"sw.js"*) echo "const CACHE = 'school-v3';" ;;
    esac ;;
  logs)
    if [[ -e "$NEW" && "${FAKE_FAIL:-}" == logs ]]; then exit 1; fi
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
    --max-time|--noproxy) shift 2 ;;
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
printf 'df %s\n' "$*" >> "$SHIM_LOG"
kb="${FAKE_DF_AVAIL_KB:-104857600}"
[[ -z "${FAKE_DF_LOW_PATH:-}" || "${!#}" != *"$FAKE_DF_LOW_PATH"* ]] || kb=1024
printf 'Filesystem 1024-blocks Used Available Capacity Mounted\nfake 999999999 1 %s 1%% /\n' "$kb"
"""

SSH_SHIM = '#!/usr/bin/env bash\nprintf \'ssh %s\\n\' "$*" >> "$SHIM_LOG"\nexit 0\n'

SMOKE_OK = "#!/usr/bin/env bash\nexit 0\n"
SMOKE_BAD = "#!/usr/bin/env bash\necho 'sync_meta ixl FAIL last_result=error'\necho 'SMOKE: FAIL'\nexit 1\n"


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
    (deploy / "docker-compose.yml").write_text(f"services:\n  dashboard:\n    image: {IMG_REPO}:latest\n")
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
    return sorted(d for d in root.iterdir() if d.name.startswith("pre-")) if root.exists() else []


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
    p = run(env, "--slug", "spa", "--smoke")
    assert p.returncode == 0, p.stdout + p.stderr
    calls = log(env)
    assert not any(c.startswith("ssh") for c in calls)
    tags = [c for c in calls if c.startswith("docker tag ")]
    assert len(tags) == 1
    assert re.fullmatch(rf"docker tag sha256:0123456789abcdef {re.escape(IMG_REPO)}:rollback-\d{{8}}T\d{{6}}Z", tags[0])
    pull = next(i for i, c in enumerate(calls) if " pull" in c)
    up = next(i for i, c in enumerate(calls) if " up -d" in c)
    assert calls.index(tags[0]) < pull < up
    assert all("--noproxy *" in c for c in calls if c.startswith("curl"))
    assert any(c.startswith("df ") and "/var/lib/docker" in c for c in calls)
    assert not any("--force-recreate" in c for c in calls)
    (b,) = backups(env)
    assert re.fullmatch(r"pre-spa-\d{8}T\d{6}Z", b.name)
    assert stat.S_IMODE((b / "env").stat().st_mode) == 0o600
    assert (b / "env").read_text() == f"SYNC_TOKEN={SECRET}\n"
    assert (b / "state" / "school-state.json").exists()
    assert (b / "docker-compose.yml").exists()
    assert "DEPLOY: OK" in p.stdout
    assert_no_secret(p)


def _reset_shim(env):
    for f in Path(env["SHIM_STATE"]).iterdir():
        f.unlink()


def test_known_watchdog_problems(env):
    # the shim reports ixl:stale before AND after: it is in the baseline, so it never fails the gate
    assert run(env, "--slug", "spa").returncode == 0
    # a problem that appears only after the deploy fails the gate and rolls back; the pre-existing
    # one must not make the rollback verification fail (exit 1, not 3)
    _reset_shim(env)
    p = run(env, "--slug", "spa3", FAKE_FAIL="watchdog")
    assert p.returncode == 1, p.stdout + p.stderr
    assert "watchdog" in p.stdout
    # KNOWN_WATCHDOG_PROBLEMS is an additive override
    _reset_shim(env)
    assert run(env, "--slug", "spa4", FAKE_FAIL="watchdog", KNOWN_WATCHDOG_PROBLEMS="gc:failing").returncode == 0


def test_baseline_taken_before_pull(env):
    run(env, "--slug", "spa")
    calls = log(env)
    base = next(i for i, c in enumerate(calls) if "watchdog --dry-run" in c)
    assert base < next(i for i, c in enumerate(calls) if " pull" in c)


# --- gate failures -> rollback ------------------------------------------------

GATE_FAILS = [
    "pull", "up",
    "route:/api/dashboard", "route:/api/overview", "route:/api/readiness",
    "route:/api/sync/status", "route:/api/sync/meta", "route:/app/", "route:/legacy",
    "root", "sw", "maint", "traceback", "watchdog",
]


@pytest.mark.parametrize("fail", GATE_FAILS)
def test_gate_failure_rolls_back(env, fail):
    p = run(env, "--slug", "spa", FAKE_FAIL=fail)
    assert p.returncode == 1, p.stdout + p.stderr
    calls = log(env)
    tag = re.search(r"docker tag \S+ (\S+:rollback-\S+)", "\n".join(calls)).group(1)
    retag = f"docker tag {tag} {IMG_REPO}:latest"
    assert retag in calls
    recreate = next(i for i, c in enumerate(calls) if "up -d --force-recreate" in c)
    assert calls.index(retag) < recreate
    after = calls[recreate:]
    assert any("maintenance check" in c for c in after) and any("/api/dashboard" in c for c in after), \
        "gates must run after the rollback recreate"
    assert "ROLLBACK" in p.stdout
    assert_no_secret(p)


def test_smoke_failure_rolls_back(env):
    _write(Path(env["SMOKE_SCRIPT"]), SMOKE_BAD)
    p = run(env, "--smoke")
    assert p.returncode == 1
    assert any("up -d --force-recreate" in c for c in log(env))


def test_no_smoke_unless_requested(env):
    _write(Path(env["SMOKE_SCRIPT"]), SMOKE_BAD)
    assert run(env).returncode == 0


def test_failed_rollback_verification_exits_3(env):
    p = run(env, FAKE_FAIL="route:/app/", STICKY="1")
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
    p = run(env, "--rollback", "rollback-20260101T000000Z")
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
    p = run(env, "--rollback", "t1", FAKE_FAIL="route:/app/", STICKY="1")
    assert p.returncode == 3


# --- live-check.mjs -----------------------------------------------------------

def test_live_check_rejects_bad_base_url():
    node = subprocess.run(["bash", "-c", "command -v node"], capture_output=True, text=True)
    if node.returncode != 0:
        pytest.skip("node not installed")
    live = Path(SCRIPT).parent / "live-check.mjs"
    p = subprocess.run(["node", str(live), "http://a;b"], capture_output=True, text=True)
    assert p.returncode == 2


# --- review round 1 -----------------------------------------------------------

def test_backup_exists_before_pull(env):
    assert run(env, "--slug", "spa").returncode == 0
    assert "SAW_BACKUP" in log(env)


def test_rollback_must_land_on_previous_image(env):
    p = run(env, FAKE_FAIL="route:/app/", IMG_STUCK="1")
    assert p.returncode == 3, p.stdout + p.stderr
    assert "image" in p.stdout.lower()


def test_restore_tag_failure_aborts_without_recreate(env):
    p = run(env, FAKE_FAIL="logs", FAKE_TAG_RESTORE_FAIL="1")
    assert p.returncode == 3, p.stdout + p.stderr
    assert not any("--force-recreate" in c for c in log(env))


def test_compose_tag_must_be_latest(env):
    (Path(env["DEPLOY_DIR"]) / "docker-compose.yml").write_text(f"    image: {IMG_REPO}:v2\n")
    p = run(env)
    assert p.returncode == 2 and "latest" in p.stderr
    _nothing_changed(env)


def test_compose_without_tag_is_latest(env):
    (Path(env["DEPLOY_DIR"]) / "docker-compose.yml").write_text(f"    image: {IMG_REPO}\n")
    assert run(env).returncode == 0


@pytest.mark.parametrize("fail", ["tag", "inspect"])
def test_prepull_step_failures_exit_2(env, fail):
    p = run(env, FAKE_FAIL=fail)
    assert p.returncode == 2, p.stdout + p.stderr
    assert not any(" pull" in c for c in log(env))


def test_backup_dir_failure_exits_2(env):
    blocker = Path(env["TMPDIR"]) / "blocker"
    blocker.write_text("x")
    p = run(env, BACKUP_ROOT=str(blocker / "sub"))
    assert p.returncode == 2
    assert not any(" pull" in c for c in log(env))


def test_symlinked_state_dir_is_backed_up(env):
    deploy = Path(env["DEPLOY_DIR"])
    real = Path(env["TMPDIR"]) / "real-state"
    (deploy / "state").rename(real)
    (deploy / "state").symlink_to(real)
    assert run(env, "--slug", "spa").returncode == 0
    (b,) = backups(env)
    assert not (b / "state").is_symlink() and (b / "state" / "school-state.json").exists()


def test_concurrent_deploy_refused_by_lock(env):
    import fcntl

    root = Path(env["BACKUP_ROOT"])
    root.mkdir(mode=0o700)
    with open(root / ".deploy.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        p = run(env)
    assert p.returncode == 2 and "another deploy" in p.stderr
    assert not any(" pull" in c for c in log(env))


def test_sync_started_after_backup_blocks_pull(env):
    p = run(env, FAKE_SYNC_AFTER_TAG="1")
    assert p.returncode == 2 and "sync" in p.stderr.lower()
    assert not any(" pull" in c for c in log(env))


@pytest.mark.parametrize("low", ["/var/lib/docker", "backups"])
def test_disk_preflight_covers_docker_root_and_backup_root(env, low):
    Path(env["BACKUP_ROOT"]).mkdir()
    p = run(env, FAKE_DF_LOW_PATH=low)
    assert p.returncode == 2 and "free" in p.stderr.lower()
    _nothing_changed(env)


def test_log_read_failure_fails_traceback_gate(env):
    assert run(env, FAKE_FAIL="logs").returncode == 1


def test_smoke_failure_shows_tail_without_secrets(env):
    _write(Path(env["SMOKE_SCRIPT"]), SMOKE_BAD)
    p = run(env, "--smoke")
    assert "SMOKE: FAIL" in p.stdout
    assert_no_secret(p)


def test_manual_rollback_without_container_uses_compose_repo(env):
    p = run(env, "--rollback", "rollback-1", FAKE_FAIL="nocontainer")
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"docker tag {IMG_REPO}:rollback-1 {IMG_REPO}:latest" in log(env)


def _spawn(env, *args, **extra):
    return subprocess.Popen(["bash", str(SCRIPT), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env={**env, **extra}, cwd=env["TMPDIR"])


def _wait_for(env, needle, timeout=10):
    import time

    end = time.time() + timeout
    while time.time() < end:
        if any(needle in c for c in log(env)):
            return
        time.sleep(0.05)
    raise AssertionError(f"never saw {needle!r}")


def test_interrupt_after_pull_started_rolls_back(env):
    import signal

    proc = _spawn(env, "--slug", "spa", FAKE_HANG="pull")
    _wait_for(env, " pull")
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 1, out + err
    calls = log(env)
    assert any("up -d --force-recreate" in c for c in calls)


def test_interrupt_before_pull_exits_2_without_compose(env):
    import signal

    proc = _spawn(env, "--slug", "spa", FAKE_HANG="tag")
    _wait_for(env, "docker tag")
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 2, out + err
    assert not any(c.startswith("docker compose") for c in log(env))


# --- live-check.mjs end to end --------------------------------------------------

REPO = SCRIPT.parent.parent
LIVE = SCRIPT.parent / "live-check.mjs"


def _live_ready():
    if subprocess.run(["bash", "-c", "command -v node"], capture_output=True).returncode != 0:
        return "node not installed"
    if not (REPO / "web/spa/node_modules/@playwright").exists():
        return "web/spa/node_modules missing (npm --prefix web/spa ci)"
    if not (REPO / "web/spa/dist/index.html").exists():
        return "web/spa not built (npm --prefix web/spa run build)"
    probe = subprocess.run(["node", "-e",
                            "const {chromium}=require('@playwright/test');process.exit(require('fs').existsSync(chromium.executablePath())?0:1)"],
                           cwd=REPO / "web/spa", capture_output=True)
    if probe.returncode != 0:
        return "Playwright Chromium not installed"
    return None


def test_live_check_against_synthetic_server(tmp_path):
    import socket
    import time
    import urllib.request

    why = _live_ready()
    if why:
        pytest.skip(why)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen(["python3", str(REPO / "scripts/spa-qa-server.py"), "--port", str(port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=REPO)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/app/", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("synthetic server did not start")
        p = subprocess.run(["node", str(LIVE), f"http://127.0.0.1:{port}"], capture_output=True, text=True,
                           env={**os.environ, "LIVE_SHOTS": str(tmp_path / "shots")}, timeout=600)
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "LIVE-CHECK: PASS" in p.stdout
    assert "screens=40 " in p.stdout and "failed_screens=0" in p.stdout
    assert len(list((tmp_path / "shots").glob("*.png"))) == 40


def test_live_check_prints_summary_and_fails_when_server_down(tmp_path):
    why = _live_ready()
    if why:
        pytest.skip(why)
    p = subprocess.run(["node", str(LIVE), "http://127.0.0.1:9"], capture_output=True, text=True,
                       env={**os.environ, "LIVE_SHOTS": str(tmp_path)}, timeout=600)
    assert p.returncode == 1, p.stdout + p.stderr
    assert re.search(r"live-check: screens=40 failed_screens=40 ", p.stdout)
    assert "LIVE-CHECK: FAIL" in p.stdout
