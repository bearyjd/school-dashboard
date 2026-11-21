"""run_scraper: real subprocesses; a timeout kills the whole process group (children included)."""
import dataclasses
import os
import signal
import time
from pathlib import Path

import pytest

from school_dashboard.scrape_run import MAX_STDERR_BYTES, ScrapeRun, run_scraper


def _alive(pid: int) -> bool:
    """True while the process exists and is not a zombie."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def _wait_gone(pids: list[int], within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return False


def _read_pids(path: Path) -> list[int]:
    return [int(x) for x in path.read_text().split()]


def _script(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "scraper.sh"
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(0o755)
    return [str(p), str(tmp_path / "pids")]


def _cleanup(path: Path) -> None:
    if path.exists():
        for pid in _read_pids(path):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_scrape_run_is_frozen():
    run = ScrapeRun(returncode=0, stdout="", stderr="", timed_out=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(run, "returncode", 1)


def test_normal_exit_returns_stdout_and_stderr(tmp_path):
    cmd = _script(tmp_path, 'echo \'{"ok": true}\'; echo oops >&2; exit 3\n')
    run = run_scraper(cmd, timeout=10)
    assert run == ScrapeRun(returncode=3, stdout='{"ok": true}\n', stderr="oops\n", timed_out=False)


def test_env_is_passed(tmp_path):
    cmd = _script(tmp_path, 'echo "$FAKE_VAR"\n')
    assert run_scraper(cmd, timeout=10, env={**os.environ, "FAKE_VAR": "hello"}).stdout == "hello\n"


def test_timeout_kills_the_whole_group(tmp_path):
    pids = tmp_path / "pids"
    cmd = _script(tmp_path, 'sleep 300 &\necho "$$ $!" > "$1"\nsleep 300\n')
    try:
        start = time.monotonic()
        run = run_scraper(cmd, timeout=1)
        assert time.monotonic() - start < 15
        assert run.timed_out is True
        assert len(_read_pids(pids)) == 2
        assert _wait_gone(_read_pids(pids)), "scraper or its child survived the timeout"
    finally:
        _cleanup(pids)


def test_scraper_ignoring_sigterm_is_sigkilled(tmp_path):
    pids = tmp_path / "pids"
    cmd = _script(tmp_path, "trap '' TERM\nsleep 300 &\necho \"$$ $!\" > \"$1\"\nwhile true; do sleep 1; done\n")
    try:
        start = time.monotonic()
        run = run_scraper(cmd, timeout=1)
        assert time.monotonic() - start < 20
        assert run.timed_out is True
        assert _wait_gone(_read_pids(pids))
    finally:
        _cleanup(pids)


def test_children_left_behind_after_a_normal_exit_are_killed(tmp_path):
    pids = tmp_path / "pids"
    cmd = _script(tmp_path, 'sleep 300 >/dev/null 2>&1 &\necho "$$ $!" > "$1"\nexit 0\n')
    try:
        run = run_scraper(cmd, timeout=10)
        assert run.returncode == 0 and run.timed_out is False
        assert _wait_gone(_read_pids(pids))
    finally:
        _cleanup(pids)


def test_large_stderr_is_bounded_and_keeps_the_tail(tmp_path):
    cmd = _script(tmp_path, "head -c 400000 /dev/zero | tr '\\0' x >&2\necho TAIL >&2\necho out\n")
    run = run_scraper(cmd, timeout=20)
    assert run.returncode == 0 and run.stdout == "out\n"
    assert len(run.stderr.encode()) <= MAX_STDERR_BYTES
    assert run.stderr.endswith("TAIL\n")


def test_missing_command_returns_a_failure_without_raising():
    run = run_scraper(["/nonexistent/scraper-xyz"], timeout=5)
    assert run.returncode != 0 and run.timed_out is False
    assert "scraper-xyz" in run.stderr
