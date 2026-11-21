"""Run a scraper CLI in its own process group; kill the whole group on timeout.

Scrapers start chromium and other children. subprocess.run(timeout=...) kills only the
direct child, so a hung scrape would leave browsers behind. run_scraper never raises on
timeout, always returns after the group is gone, and bounds the output it keeps.
"""
import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import IO

logger = logging.getLogger(__name__)

MAX_STDERR_BYTES = 64 * 1024  # keep the last 64 KiB of stderr
MAX_STDOUT_BYTES = 16 * 1024 * 1024  # a scrape document; anything beyond is dropped and flagged
TERM_GRACE_SECONDS = 10.0
_CHUNK = 64 * 1024
_START_FAILED = 127
_JOIN_SECONDS = 2.0  # total wait for both reader threads


@dataclass(frozen=True)
class ScrapeRun:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool = False  # stdout hit the cap; the document is incomplete


class ScraperTimeout(RuntimeError):
    """A scraper exceeded its timeout (the group was killed)."""

    def __init__(self, source: str, timeout: float) -> None:
        super().__init__(f"{source}: timed out after {timeout:g} s")
        self.source = source
        self.timeout = timeout


class _Capture(threading.Thread):
    """Drain a pipe so the child never blocks on a full buffer; keep the head or the tail."""

    def __init__(self, stream: IO[bytes], limit: int, keep_tail: bool) -> None:
        super().__init__(daemon=True)
        self._stream, self._limit, self._tail = stream, limit, keep_tail
        self.data = bytearray()
        self.truncated = False

    def run(self) -> None:
        """Read until EOF, then close the stream. Only this thread ever closes it: if a detached
        grandchild keeps the pipe open past the join timeout, closing it from another thread
        while os.read is blocked would hand a recycled fd number to the reader."""
        fd = self._stream.fileno()
        try:
            while chunk := os.read(fd, _CHUNK):
                self.data += chunk
                if len(self.data) > self._limit:
                    self.truncated = self.truncated or not self._tail
                    self._trim()
        except (OSError, ValueError):
            pass
        finally:
            self._stream.close()

    def _trim(self) -> None:
        if self._tail:
            del self.data[: len(self.data) - self._limit]
        else:
            del self.data[self._limit:]


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _terminate_group(proc: "subprocess.Popen[bytes]") -> None:
    """SIGTERM the group, allow TERM_GRACE_SECONDS for it to go, then SIGKILL what is left."""
    pgid = proc.pid
    _signal_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + TERM_GRACE_SECONDS
    while time.monotonic() < deadline and (proc.poll() is None or _group_alive(pgid)):
        time.sleep(0.05)
    _signal_group(pgid, signal.SIGKILL)
    proc.wait()


def _decode(cap: _Capture) -> str:
    return bytes(cap.data).decode("utf-8", errors="replace")


def run_scraper(
    cmd: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None,
    max_stdout_bytes: int = MAX_STDOUT_BYTES,
) -> ScrapeRun:
    """Run `cmd`; returns a ScrapeRun, never raises (a start failure is returncode 127)."""
    try:
        proc = subprocess.Popen(
            list(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(env) if env is not None else None, start_new_session=True,
        )
    except OSError as exc:
        return ScrapeRun(_START_FAILED, "", f"failed to start {cmd[0]}: {exc}", False)
    assert proc.stdout is not None and proc.stderr is not None
    out = _Capture(proc.stdout, max_stdout_bytes, keep_tail=False)
    err = _Capture(proc.stderr, MAX_STDERR_BYTES, keep_tail=True)
    out.start()
    err.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        logger.warning("%s timed out after %g s; killing its process group", cmd[0], timeout)
        _terminate_group(proc)
    else:
        _signal_group(proc.pid, signal.SIGKILL)  # sweep children the scraper left behind
    deadline = time.monotonic() + _JOIN_SECONDS
    for cap in (out, err):  # a still-blocked reader keeps its fd; it is a daemon and closes it at EOF
        cap.join(timeout=max(0.0, deadline - time.monotonic()))
    return ScrapeRun(proc.returncode, _decode(out), _decode(err), timed_out, out.truncated)
