"""Atomic, owner-only file writes (tmp in the same dir + os.replace)."""
import os
import tempfile
from pathlib import Path
from typing import Union


def mkdir_private(path: Path) -> None:
    """Create `path` and any missing parents, each with mode 0700."""
    missing = []
    p = Path(path)
    while not p.exists():
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        d.mkdir(mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)


def write_atomic(path: Path, data: Union[str, bytes], mode: int = 0o600) -> None:
    """Write `data` to `path` atomically; the file is never partially visible."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
