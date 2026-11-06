"""Runs the REAL sync/gc-refresh.sh against a stub `gc` (no network, no creds)."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "sync" / "gc-refresh.sh"
FLOCK = shutil.which("flock")

needs_flock = pytest.mark.skipif(FLOCK is None, reason="flock not installed")


def _env(tmp_path: Path, *, gc_exit: int = 0, wait: int = 2, with_flock: bool = True,
         gc_body: str = ""):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "gc-ran"
    gc = bindir / "gc"
    gc.write_text(f"#!/bin/sh\necho ran >> {marker}\n{gc_body}\necho 'Token refreshed'\nexit {gc_exit}\n")
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


def _hold_lock(env, tmp_path: Path, seconds: float) -> subprocess.Popen:
    """Hold the lock in a subprocess; return only once it has really acquired it."""
    ready = tmp_path / "holder-ready"
    holder = subprocess.Popen(
        [FLOCK, env["GC_REFRESH_LOCK"], "sh", "-c", f"touch {ready}; sleep {seconds}"])
    deadline = time.monotonic() + 5
    while not ready.exists():
        if time.monotonic() > deadline:
            holder.kill()
            raise AssertionError("lock holder never became ready")
        time.sleep(0.02)
    return holder


@needs_flock
def test_refresh_runs_under_lock(tmp_path):
    env, marker = _env(tmp_path)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert marker.exists()
    assert "Token refreshed" in r.stderr
    assert "WARN" not in r.stderr


@needs_flock
def test_second_holder_blocks_until_release(tmp_path):
    env, marker = _env(tmp_path, wait=10)
    holder = _hold_lock(env, tmp_path, 1.5)
    start = time.monotonic()
    r = _run(env)
    elapsed = time.monotonic() - start
    holder.wait()
    assert r.returncode == 0, r.stderr
    assert marker.exists()
    assert elapsed >= 0.5  # waited for the holder
    assert "WARN" not in r.stderr


@needs_flock
def test_lock_timeout_warns_and_continues(tmp_path):
    env, marker = _env(tmp_path, wait=1)
    holder = _hold_lock(env, tmp_path, 4)
    try:
        r = _run(env)
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 0, r.stderr
    assert not marker.exists()
    assert "WARN" in r.stderr and "busy" in r.stderr


@needs_flock
def test_gc_failure_is_nonfatal(tmp_path):
    env, marker = _env(tmp_path, gc_exit=3)
    r = _run(env)
    assert r.returncode == 0
    assert marker.exists()
    assert "WARN: gc token-refresh exited non-zero" in r.stderr


@needs_flock
def test_orphaned_child_does_not_keep_lock(tmp_path):
    """gc's children (Playwright/Chromium) must not inherit the lock fd."""
    pidfile = tmp_path / "sleeper.pid"
    env, _ = _env(tmp_path, gc_body=f"sleep 30 >/dev/null 2>&1 &\necho $! > {pidfile}")
    try:
        r = _run(env)
        assert r.returncode == 0, r.stderr
        probe = subprocess.run([FLOCK, "-n", env["GC_REFRESH_LOCK"], "true"])
        assert probe.returncode == 0, "orphaned child still holds the lock"
    finally:
        if pidfile.exists():
            subprocess.run(["kill", pidfile.read_text().strip()], check=False)


def test_missing_flock_warns_and_still_runs(tmp_path):
    env, marker = _env(tmp_path, with_flock=False)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert marker.exists()
    assert "WARN: flock not found" in r.stderr
