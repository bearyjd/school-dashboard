"""Advisory lock serialising load -> modify -> save cycles on the state file."""
import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

LOCK_TIMEOUT = 60.0
POLL_SECONDS = 0.05


class StateLockTimeout(RuntimeError):
    pass


def lock_path(state_file: Path) -> Path:
    state_file = Path(state_file)
    return state_file.with_name(state_file.name + ".lock")


@contextmanager
def state_lock(state_file: Path, timeout: Optional[float] = None) -> Iterator[None]:
    """Hold an exclusive flock on `<state file>.lock`; fail clearly after `timeout` seconds."""
    wait = LOCK_TIMEOUT if timeout is None else timeout
    path = lock_path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StateLockTimeout(
                        f"could not acquire state lock {path} within {wait:g}s; "
                        "another school-state command is running") from None
                time.sleep(POLL_SECONDS)
        yield
    finally:
        os.close(fd)
