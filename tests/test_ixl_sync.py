"""Runs the REAL IXL section of sync/school-sync.sh against stub `ixl`/`school-state`.

Regression (2026-11-06): one child's failed login was invisible in sync status and its
previous file was clobbered with the error JSON, making stale state look fresh.
"""
import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "sync" / "school-sync.sh"
PASSWORDS = {"jack": "pw-jack-SECRET", "penn": "pw-penn-SECRET", "ford": "pw-ford-SECRET"}
OK_PAYLOAD = {"status": "ok", "warnings": [], "errors": [], "totals": {"math": {"remaining": 1}}, "remaining": []}
ERR_PAYLOAD = {
    "status": "error", "warnings": [], "remaining": [],
    "errors": [{"code": "auth.login_failed", "stage": "login", "retryable": True,
                "message": "Login failed for ford@school pw-ford-SECRET: Timeout 15000ms exceeded"}],
}


def _setup(tmp_path: Path, failing=("ford",), accounts=("jack", "penn", "ford")):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    ixl_dir = tmp_path / "ixl"
    ixl_dir.mkdir()
    (tmp_path / ".ixl").mkdir()
    (tmp_path / ".ixl" / "accounts.env").write_text(
        "".join(f"{n}:{n}user@school:{PASSWORDS[n]}\n" for n in accounts))
    # school-state: maintenance check inactive (10); `update` records itself, then exits 3
    # (maintenance) so the script stops cleanly right after the merge step.
    ss = bindir / "school-state"
    ss.write_text(f"""#!/bin/sh
if [ "$1" = transition ]; then exit 10; fi
echo "$@" >> {state}/school-state.calls
ls {ixl_dir} >> {state}/merge-saw.txt
exit 3
""")
    ss.chmod(0o755)
    ixl = bindir / "ixl"
    ixl.write_text(f"""#!/bin/sh
case "$IXL_EMAIL" in
{"|".join(f"{n}user@school" for n in failing) or "NOPE"})
  echo '{json.dumps(ERR_PAYLOAD)}'; exit 1;;
esac
echo '{json.dumps(OK_PAYLOAD)}'
""")
    ixl.chmod(0o755)
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path), "PYTHONPATH": str(REPO),
        "SCHOOL_DASHBOARD_ENV": "/nonexistent", "SCHOOL_STATE_DIR": str(state), "IXL_DIR": str(ixl_dir),
        "SOURCE_SGY_ENABLED": "0", "SOURCE_GC_ENABLED": "0", "SOURCE_POWERSCHOOL_ENABLED": "0",
    }
    return env, state, ixl_dir


def _run(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def _ixl_meta(state: Path) -> str:
    return json.loads((state / "sync_meta.json").read_text())["ixl"]["last_result"]


def _assert_no_secrets(r, state):
    blob = r.stderr + r.stdout + (state / "school-sync.log").read_text() if (state / "school-sync.log").exists() \
        else r.stderr + r.stdout
    for n, pw in PASSWORDS.items():
        assert pw not in blob
        assert f"{n}user@school" not in blob
    assert "Timeout 15000ms" not in blob


def test_partial_failure_marks_error_and_skips_failed_child(tmp_path):
    env, state, ixl_dir = _setup(tmp_path)
    (ixl_dir / "ford-assigned.json").write_text(json.dumps({"totals": {"old": 1}, "remaining": []}))
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert not (ixl_dir / "ford-assigned.json").exists()
    for name in ("jack", "penn"):
        assert json.loads((ixl_dir / f"{name}-assigned.json").read_text())["totals"]
    assert _ixl_meta(state) == "error"
    assert "WARN: IXL scrape failed for ford (exit 1)" in r.stderr
    assert "code=auth.login_failed stage=login" in r.stderr
    assert "update" in (state / "school-state.calls").read_text()
    merged = (state / "merge-saw.txt").read_text()
    assert "jack-assigned.json" in merged and "ford-assigned.json" not in merged
    assert not list(ixl_dir.glob(".*-assigned.*")), "temp files must not leak"
    _assert_no_secrets(r, state)


def test_all_success_is_ok(tmp_path):
    env, state, ixl_dir = _setup(tmp_path, failing=())
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in ixl_dir.iterdir()) == [f"{n}-assigned.json" for n in ("ford", "jack", "penn")]
    assert _ixl_meta(state) == "ok"


def test_all_fail_is_error_and_merge_still_runs(tmp_path):
    env, state, ixl_dir = _setup(tmp_path, failing=("jack", "penn", "ford"))
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert list(ixl_dir.iterdir()) == []
    assert _ixl_meta(state) == "error"
    assert "update" in (state / "school-state.calls").read_text()


def test_exit_zero_but_error_payload_is_treated_as_failure(tmp_path):
    env, state, ixl_dir = _setup(tmp_path, failing=())
    ixl = Path(env["PATH"].split(":")[0]) / "ixl"
    ixl.write_text(f"#!/bin/sh\necho '{json.dumps(ERR_PAYLOAD)}'\n")
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert list(ixl_dir.iterdir()) == []
    assert _ixl_meta(state) == "error"
