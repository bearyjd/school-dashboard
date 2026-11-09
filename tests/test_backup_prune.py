"""Retention pruning of <state>/backups: naming-pattern only, newest N kept, never incomplete."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from school_dashboard import backup_prune, cli


def _mk(root: Path, name: str, complete: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "school.db").write_text("x")
    if complete:
        (d / "manifest.json").write_text("{}")
    return d


def _stamp(i: int) -> str:
    return f"202611{i:02d}T031500000000Z"


def test_keeps_newest_n_and_removes_older(tmp_path):
    for i in range(1, 6):
        _mk(tmp_path, _stamp(i))
    removed = backup_prune.prune_backups(tmp_path, keep=2)
    assert sorted(p.name for p in removed) == [_stamp(1), _stamp(2), _stamp(3)]
    assert sorted(p.name for p in tmp_path.iterdir()) == [_stamp(4), _stamp(5)]


def test_never_deletes_incomplete_or_foreign_entries(tmp_path):
    for i in range(1, 5):
        _mk(tmp_path, _stamp(i))
    partial = _mk(tmp_path, _stamp(0), complete=False)  # no manifest
    staging = _mk(tmp_path, f".{_stamp(9)}.partial-abcd1234", complete=False)
    foreign = _mk(tmp_path, "my-manual-copy")
    stray = tmp_path / "notes.txt"
    stray.write_text("hi")
    backup_prune.prune_backups(tmp_path, keep=1)
    left = {p.name for p in tmp_path.iterdir()}
    assert {partial.name, staging.name, foreign.name, stray.name} <= left
    assert _stamp(4) in left and _stamp(1) not in left


def test_incomplete_newest_does_not_count_against_keep(tmp_path):
    _mk(tmp_path, _stamp(1))
    _mk(tmp_path, _stamp(2))
    _mk(tmp_path, _stamp(3), complete=False)
    backup_prune.prune_backups(tmp_path, keep=1)
    assert (tmp_path / _stamp(2)).exists() and not (tmp_path / _stamp(1)).exists()


def test_symlinked_entries_are_never_followed_or_removed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}")
    root = tmp_path / "backups"
    root.mkdir()
    _mk(root, _stamp(5))
    link = root / _stamp(1)
    os.symlink(outside, link)
    backup_prune.prune_backups(root, keep=1)
    assert link.is_symlink() and (outside / "manifest.json").exists()


@pytest.mark.parametrize("keep", [0, -1])
def test_prune_rejects_keep_below_one_and_deletes_nothing(tmp_path, keep):
    _mk(tmp_path, _stamp(1))
    _mk(tmp_path, _stamp(2))
    with pytest.raises(ValueError):
        backup_prune.prune_backups(tmp_path, keep=keep)
    assert len(list(tmp_path.iterdir())) == 2


def test_missing_root_is_a_noop(tmp_path):
    assert backup_prune.prune_backups(tmp_path / "nope", keep=3) == []


def test_parse_keep_default_and_invalid():
    assert backup_prune.parse_keep(None) == 14
    assert backup_prune.parse_keep("") == 14
    assert backup_prune.parse_keep("7") == 7
    for bad in ("abc", "-3", "0", "2.5"):
        with pytest.raises(ValueError):
            backup_prune.parse_keep(bad)


def test_name_pattern_rejects_traversal_and_lookalikes():
    ok = backup_prune.is_backup_name
    assert ok("20261101T031500123456Z")
    for bad in ("..", ".", "../20261101T031500123456Z", "20261101T031500123456Z/x",
                "20261101T031500123456Z.bak", "x20261101T031500123456Z", "",
                "20261101T031500123456Z\n"):
        assert not ok(bad)


def test_cli_prune_subcommand(state_env, capsys, monkeypatch):
    monkeypatch.delenv("BACKUP_KEEP", raising=False)
    root = state_env / "backups"
    for i in range(1, 4):
        _mk(root, _stamp(i))
    with patch("sys.argv", ["school-state", "transition", "prune-backups", "--keep", "1"]):
        cli.main()
    assert [p.name for p in root.iterdir()] == [_stamp(3)]
    assert "pruned 2" in capsys.readouterr().out.lower()


def test_cli_prune_reads_backup_keep_env(state_env, monkeypatch):
    monkeypatch.setenv("BACKUP_KEEP", "2")
    root = state_env / "backups"
    for i in range(1, 4):
        _mk(root, _stamp(i))
    with patch("sys.argv", ["school-state", "transition", "prune-backups"]):
        cli.main()
    assert sorted(p.name for p in root.iterdir()) == [_stamp(2), _stamp(3)]


@pytest.mark.parametrize("argv", [["--keep", "0"], ["--keep", "-2"], ["--keep", "abc"]])
def test_cli_rejects_bad_keep_with_exit_2_and_deletes_nothing(state_env, argv):
    root = state_env / "backups"
    for i in range(1, 4):
        _mk(root, _stamp(i))
    with patch("sys.argv", ["school-state", "transition", "prune-backups", *argv]):
        with pytest.raises(SystemExit) as exc:
            cli.main()
    assert exc.value.code == 2
    assert len(list(root.iterdir())) == 3


@pytest.mark.parametrize("raw", ["0", "-1", "lots"])
def test_cli_bad_env_keep_logs_error_exit_2_and_deletes_nothing(state_env, monkeypatch, caplog, raw):
    monkeypatch.setenv("BACKUP_KEEP", raw)
    root = state_env / "backups"
    for i in range(1, 4):
        _mk(root, _stamp(i))
    with patch("sys.argv", ["school-state", "transition", "prune-backups"]):
        with caplog.at_level("ERROR"), pytest.raises(SystemExit) as exc:
            cli.main()
    assert exc.value.code == 2
    assert "BACKUP_KEEP" in caplog.text
    assert len(list(root.iterdir())) == 3


# ---- stale partial (staging) dirs ----

def _partial(root: Path, i: int, age_h: float, now: float) -> Path:
    d = _mk(root, f".{_stamp(i)}.partial-abcd12{i:02d}", complete=False)
    os.utime(d, (now - age_h * 3600, now - age_h * 3600))
    return d


def test_stale_partials_older_than_24h_are_removed(tmp_path):
    import time
    now = time.time()
    _mk(tmp_path, _stamp(1))
    old = _partial(tmp_path, 2, 30, now)
    fresh = _partial(tmp_path, 3, 2, now)
    removed = backup_prune.prune_backups(tmp_path, keep=5, now=now)
    assert old in removed and not old.exists() and fresh.exists()
    assert (tmp_path / _stamp(1)).exists()


def test_partial_cleanup_is_strict_about_names_and_symlinks(tmp_path):
    import time
    now = time.time()
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "backups"
    root.mkdir()
    link = root / f".{_stamp(1)}.partial-abcd1234"
    os.symlink(outside, link)
    lookalike = _partial(root, 2, 48, now)
    bad = root / ".my-notes.partial-abcd1234"
    bad.mkdir()
    os.utime(bad, (now - 48 * 3600, now - 48 * 3600))
    wrong = root / f".{_stamp(3)}.partial-ZZZZ"
    wrong.mkdir()
    os.utime(wrong, (now - 48 * 3600, now - 48 * 3600))
    backup_prune.prune_backups(root, keep=5, now=now)
    assert link.is_symlink() and outside.exists() and bad.exists() and wrong.exists()
    assert not lookalike.exists()
