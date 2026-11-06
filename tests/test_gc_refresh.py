"""Runs the REAL sync/gc-refresh.sh against a stub `gc` (no network, no creds)."""
import os
import shutil
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "sync" / "gc-refresh.sh"
FLOCK = shutil.which("flock")


def _env(tmp_path: Path, *, gc_exit: int = 0, wait: int = 2, with_flock: bool = True):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "gc-ran"
    gc = bindir / "gc"
    gc.write_text(f"#!/bin/sh\necho ran >> {marker}\necho 'Token refreshed'\nexit {gc_exit}\n")
    gc.chmod(0o755)
    if with_flock:
        path = f"{bindir}:{os.environ['PATH']}"
    else:
        # minimal PATH with the tools the script needs, but no flock
        for tool in ("bash", "sed", "date", "env"):
            (bindir / tool).symlink_to(shutil.which(tool))
        path = str(bindir)
    env = {
        "PATH": path, "HOME": str(tmp_path),
        "GC_REFRESH_LOCK": str(tmp_path / "refresh.lock"),
        "GC_REFRESH_LOCK_WAIT": str(wait),
    }
    return env, marker


def _run(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)


def test_refresh_runs_under_lock(tmp_path):
    env, marker = _env(tmp_path)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert marker.exists()
    assert "Token refreshed" in r.stderr
    assert "WARN" not in r.stderr


def test_second_holder_blocks_until_release(tmp_path):
    env, marker = _env(tmp_path, wait=10)
    holder = subprocess.Popen([FLOCK, env["GC_REFRESH_LOCK"], "sleep", "1.5"])
    time.sleep(0.4)
    start = time.monotonic()
    r = _run(env)
    elapsed = time.monotonic() - start
    holder.wait()
    assert r.returncode == 0, r.stderr
    assert marker.exists()
    assert elapsed >= 0.8  # waited for the holder
    assert "WARN" not in r.stderr


def test_lock_timeout_warns_and_continues(tmp_path):
    env, marker = _env(tmp_path, wait=1)
    holder = subprocess.Popen([FLOCK, env["GC_REFRESH_LOCK"], "sleep", "4"])
    time.sleep(0.4)
    try:
        r = _run(env)
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 0, r.stderr
    assert not marker.exists()
    assert "WARN" in r.stderr and "busy" in r.stderr


def test_gc_failure_is_nonfatal(tmp_path):
    env, marker = _env(tmp_path, gc_exit=3)
    r = _run(env)
    assert r.returncode == 0
    assert marker.exists()
    assert "WARN: gc token-refresh exited non-zero" in r.stderr


def test_missing_flock_warns_and_still_runs(tmp_path):
    env, marker = _env(tmp_path, with_flock=False)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert marker.exists()
    assert "WARN: flock not found" in r.stderr
