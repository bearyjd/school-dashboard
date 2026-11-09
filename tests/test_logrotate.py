"""Size-based copytruncate log rotation for <state>/*.log."""
import gzip
import os
from pathlib import Path

from school_dashboard import logrotate


def _log(d: Path, name: str, size: int, fill: str = "a") -> Path:
    p = d / name
    p.write_text(fill * size)
    return p


def test_small_logs_are_untouched(tmp_path):
    p = _log(tmp_path, "digest.log", 10)
    assert logrotate.rotate_logs(tmp_path, max_bytes=100, keep=4) == []
    assert p.read_text() == "a" * 10 and not (tmp_path / "digest.log.1.gz").exists()


def test_large_log_is_copytruncated_and_compressed(tmp_path):
    p = _log(tmp_path, "digest.log", 200, "z")
    inode = p.stat().st_ino
    assert logrotate.rotate_logs(tmp_path, max_bytes=100, keep=4) == [p]
    assert p.stat().st_size == 0 and p.stat().st_ino == inode  # same file: appenders keep working
    assert gzip.decompress((tmp_path / "digest.log.1.gz").read_bytes()) == b"z" * 200
    with open(p, "a") as f:  # >> after rotation still works
        f.write("new\n")
    assert p.read_text() == "new\n"


def test_rotations_shift_and_oldest_is_dropped(tmp_path):
    for gen in range(1, 7):
        _log(tmp_path, "x.log", 200, str(gen))
        logrotate.rotate_logs(tmp_path, max_bytes=100, keep=4)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["x.log", "x.log.1.gz", "x.log.2.gz", "x.log.3.gz", "x.log.4.gz"]
    assert gzip.decompress((tmp_path / "x.log.1.gz").read_bytes()) == b"6" * 200
    assert gzip.decompress((tmp_path / "x.log.4.gz").read_bytes()) == b"3" * 200


def test_only_regular_dot_log_files_are_rotated(tmp_path):
    real = _log(tmp_path, "real.txt", 500)
    target = _log(tmp_path, "target.dat", 500)
    os.symlink(target, tmp_path / "link.log")
    (tmp_path / "dir.log").mkdir()
    logrotate.rotate_logs(tmp_path, max_bytes=100, keep=4)
    assert real.stat().st_size == 500 and target.stat().st_size == 500
    assert not list(tmp_path.glob("*.gz"))


def test_rotated_files_are_private(tmp_path):
    _log(tmp_path, "a.log", 200)
    logrotate.rotate_logs(tmp_path, max_bytes=100, keep=4)
    assert (tmp_path / "a.log.1.gz").stat().st_mode & 0o777 == 0o600


def test_missing_dir_is_noop(tmp_path):
    assert logrotate.rotate_logs(tmp_path / "nope", max_bytes=1, keep=4) == []


def test_env_parsing(monkeypatch):
    default = 5 * 1024 * 1024
    monkeypatch.delenv("LOG_MAX_BYTES", raising=False)
    assert logrotate.max_bytes_from_env() == default
    monkeypatch.setenv("LOG_MAX_BYTES", "1234")
    assert logrotate.max_bytes_from_env() == 1234
    monkeypatch.setenv("LOG_MAX_BYTES", "junk")
    assert logrotate.max_bytes_from_env() == default


def test_main_rotates_state_dir(state_env):
    _log(state_env, "backup.log", 50)
    assert logrotate.main(["--max-bytes", "10"]) == 0
    assert (state_env / "backup.log.1.gz").exists()
