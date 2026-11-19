"""Size-based, copytruncate rotation of <state>/*.log (wrappers append with >>).

Rotated copies are gzip'd as <name>.log.1.gz (newest) .. <name>.log.<keep>.gz.
The live file is truncated in place so open appenders keep working; lines
written between the copy and the truncate can be lost (same as logrotate's
copytruncate). Run daily from cron: python3 -m school_dashboard.logrotate
"""
import argparse
import gzip
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from school_dashboard import paths

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_KEEP = 4


def max_bytes_from_env() -> int:
    raw = os.environ.get("LOG_MAX_BYTES", "")
    try:
        value = int(raw) if raw else DEFAULT_MAX_BYTES
    except ValueError:
        logger.warning("LOG_MAX_BYTES=%r is not an integer; using %d", raw, DEFAULT_MAX_BYTES)
        return DEFAULT_MAX_BYTES
    return value if value > 0 else DEFAULT_MAX_BYTES


def _rotated(log: Path, n: int) -> Path:
    return log.with_name(f"{log.name}.{n}.gz")


def _shift(log: Path, keep: int) -> None:
    _rotated(log, keep).unlink(missing_ok=True)
    for n in range(keep - 1, 0, -1):
        src = _rotated(log, n)
        if src.exists():
            os.replace(src, _rotated(log, n + 1))


def _compress_head(log: Path, size: int, keep: int) -> None:
    """Gzip the first `size` bytes of log to <log>.1.gz (0600), then truncate it."""
    _shift(log, keep)
    dst = _rotated(log, 1)
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as gz, open(log, "rb") as src:
        remaining = size
        while remaining > 0:
            chunk = src.read(min(1 << 20, remaining))
            if not chunk:
                break
            gz.write(chunk)
            remaining -= len(chunk)
    os.truncate(log, 0)


def rotate_logs(directory: Path, max_bytes: int = DEFAULT_MAX_BYTES, keep: int = DEFAULT_KEEP) -> list[Path]:
    """Rotate every regular, non-symlink *.log over max_bytes; return the rotated files."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    rotated = []
    for log in sorted(directory.glob("*.log")):
        if log.is_symlink() or not log.is_file():
            continue
        size = log.stat().st_size
        if size <= max_bytes:
            continue
        try:
            _compress_head(log, size, max(1, keep))
        except OSError as exc:
            logger.error("could not rotate %s: %s", log, exc)
            continue
        rotated.append(log)
    return rotated


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Rotate state-dir *.log files (copytruncate, gzip).")
    ap.add_argument("--max-bytes", type=int, default=None, help="rotate files larger than this (env LOG_MAX_BYTES, default 5 MiB)")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="rotations to keep (default 4)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    max_bytes = args.max_bytes if args.max_bytes is not None else max_bytes_from_env()
    done = rotate_logs(paths.state_dir(), max_bytes, args.keep)
    logger.info("logrotate: rotated %d file(s)", len(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
