"""transition CLI: maintenance, backup, restore."""
import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from school_dashboard import cli, db, maintenance, paths, state

SECRET = "SUPERSECRET-TOKEN-VALUE"


def run(*argv):
    """Run school-state; return exit code (0 when main returns)."""
    with patch("sys.argv", ["school-state", *argv]):
        try:
            cli.main()
        except SystemExit as e:
            return e.code if e.code is not None else 0
    return 0


@pytest.fixture
def populated(state_env):
    dbp = str(paths.db_path())
    db.init_db(dbp)
    db.create_item(dbp, child="Ford", title="Read ch 3")
    db.create_item(dbp, child="Jack", title="Math")
    db.create_digest(dbp, "Morning", [{"t": 1}])
    s = state.load(str(paths.state_path()))
    state.add_action_item(s, child="Ford", source="manual", item_type="task",
                          summary="bring form", due=None)
    state.save(s, str(paths.state_path()))
    paths.facts_path().write_text('[{"subject": "x"}]')
    # secret-ish files that must never be backed up
    (state_env / "config").mkdir()
    (state_env / "config" / "env").write_text(f"LITELLM_API_KEY={SECRET}")
    (state_env / ".env").write_text(f"X={SECRET}")
    (state_env / "secrets").mkdir()
    (state_env / "secrets" / "token").write_text(SECRET)
    (state_env / "gc-token.json").write_text(SECRET)
    return state_env


def all_backup_text(d):
    out = ""
    for root, _, files in os.walk(d):
        for f in files:
            if f != "school.db":
                out += open(os.path.join(root, f), errors="ignore").read()
    return out


# ── maintenance subcommands ──────────────────────────────────────────────────

def test_maintenance_on_off_status_check(state_env, capsys):
    assert run("transition", "maintenance", "check") == 10
    assert run("transition", "maintenance", "on", "--reason", "upgrade") == 0
    assert run("transition", "maintenance", "check") == 0
    assert run("transition", "maintenance", "status") == 0
    assert "upgrade" in capsys.readouterr().out
    assert run("transition", "maintenance", "off") == 0
    assert run("transition", "maintenance", "check") == 10


# ── backup ───────────────────────────────────────────────────────────────────

def test_backup_refuses_when_inactive(populated, capsys):
    assert run("transition", "backup") == 2
    assert "maintenance" in capsys.readouterr().err.lower()
    assert not (populated / "backups").exists()


def test_backup_default_dest_and_manifest(populated):
    maintenance.enable("t")
    op = maintenance.status()["operation_id"]
    assert run("transition", "backup") == 0
    dirs = list((populated / "backups").iterdir())
    assert len(dirs) == 1
    b = dirs[0]
    m = json.loads((b / "manifest.json").read_text())
    assert m["operation_id"] == op
    assert m["counts"]["items"] == 2 and m["counts"]["digests"] == 1
    assert m["counts"]["action_items"] == 1
    names = {f["name"] for f in m["files"]}
    assert {"school.db", "school-state.json", "facts.json"} <= names
    assert "email-digest.json" in m["absent"]
    for f in m["files"]:
        data = (b / f["name"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"]
        assert len(data) == f["bytes"]
        assert stat.S_IMODE((b / f["name"]).stat().st_mode) == 0o600
    assert stat.S_IMODE(b.stat().st_mode) == 0o700
    assert SECRET not in all_backup_text(b)
    assert SECRET not in json.dumps(m)


def test_backup_excludes_secrets_and_never_nests(populated, tmp_path):
    maintenance.enable("t")
    assert run("transition", "backup") == 0
    assert run("transition", "backup", "--dest", str(tmp_path / "second")) == 0
    second = tmp_path / "second"
    listing = {p.name for p in second.rglob("*")}
    assert listing <= {"manifest.json", "school.db", "school-state.json", "facts.json"}
    assert not any(p.name == "backups" for p in second.rglob("*"))
    # a second default-dest backup does not include the first one
    assert run("transition", "backup") == 0
    for d in (populated / "backups").iterdir():
        assert not (d / "backups").exists()


def test_backup_refuses_nonempty_dest(populated, tmp_path):
    maintenance.enable("t")
    d = tmp_path / "x"
    d.mkdir()
    (d / "f").write_text("1")
    assert run("transition", "backup", "--dest", str(d)) == 1


def test_backup_includes_roster_config(populated, tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"children": {"Ford": {"grade": 5}}}')
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    maintenance.enable("t")
    dest = tmp_path / "b"
    assert run("transition", "backup", "--dest", str(dest)) == 0
    assert (dest / "config.json").read_text() == cfg.read_text()


# ── restore ──────────────────────────────────────────────────────────────────

@pytest.fixture
def backup_dir(populated, tmp_path):
    maintenance.enable("t")
    dest = tmp_path / "bk"
    assert run("transition", "backup", "--dest", str(dest)) == 0
    maintenance.disable()
    return dest


def rows(path, table):
    with sqlite3.connect(path) as c:
        return c.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def test_round_trip_into_empty_target(populated, backup_dir, tmp_path, capsys):
    target = tmp_path / "restored"
    assert run("transition", "restore", str(backup_dir), "--target", str(target)) == 0
    assert "ok" in capsys.readouterr().out.lower()
    assert rows(target / "school.db", "items") == rows(paths.db_path(), "items")
    assert rows(target / "school.db", "digests") == rows(paths.db_path(), "digests")
    assert (target / "school-state.json").read_bytes() == paths.state_path().read_bytes()
    assert (target / "facts.json").read_bytes() == paths.facts_path().read_bytes()
    assert stat.S_IMODE((target / "school.db").stat().st_mode) == 0o600


def test_restore_into_existing_empty_dir(backup_dir, tmp_path):
    target = tmp_path / "e"
    target.mkdir()
    assert run("transition", "restore", str(backup_dir), "--target", str(target)) == 0


def test_restore_requires_target(backup_dir):
    assert run("transition", "restore", str(backup_dir)) == 2  # argparse error


def test_restore_refuses_nonempty_target(backup_dir, tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    (target / "keep").write_text("x")
    assert run("transition", "restore", str(backup_dir), "--target", str(target)) != 0
    assert [p.name for p in target.iterdir()] == ["keep"]


def test_tampered_file_rejected_target_untouched(backup_dir, tmp_path, capsys):
    (backup_dir / "facts.json").write_text('[{"subject": "evil"}]')
    target = tmp_path / "t"
    assert run("transition", "restore", str(backup_dir), "--target", str(target)) == 1
    assert not target.exists()
    assert "sha256" in capsys.readouterr().err.lower()


def test_tampered_db_rejected(backup_dir, tmp_path):
    with open(backup_dir / "school.db", "ab") as f:
        f.write(b"x")
    target = tmp_path / "t"
    assert run("transition", "restore", str(backup_dir), "--target", str(target)) == 1
    assert not target.exists()


@pytest.mark.parametrize("bad", ["../evil.json", "/etc/passwd", "sub/facts.json",
                                 "..", "unknown.txt", "config/env"])
def test_bad_manifest_names_rejected(backup_dir, tmp_path, bad):
    mp = backup_dir / "manifest.json"
    m = json.loads(mp.read_text())
    m["files"].append({"name": bad, "sha256": "0" * 64, "bytes": 0})
    mp.write_text(json.dumps(m))
    target = tmp_path / "t"
    assert run("transition", "restore", str(backup_dir), "--target", str(target)) == 1
    assert not target.exists()
    assert not (tmp_path / "evil.json").exists()


def test_missing_manifest_rejected(tmp_path):
    (tmp_path / "empty").mkdir()
    assert run("transition", "restore", str(tmp_path / "empty"),
               "--target", str(tmp_path / "t")) == 1


def test_symlinked_backup_file_rejected(backup_dir, tmp_path):
    real = tmp_path / "real.json"
    real.write_bytes((backup_dir / "facts.json").read_bytes())
    (backup_dir / "facts.json").unlink()
    (backup_dir / "facts.json").symlink_to(real)
    assert run("transition", "restore", str(backup_dir),
               "--target", str(tmp_path / "t")) == 1


def test_row_count_mismatch_rejected(backup_dir, tmp_path):
    mp = backup_dir / "manifest.json"
    m = json.loads(mp.read_text())
    m["counts"]["items"] = 99
    mp.write_text(json.dumps(m))
    target = tmp_path / "t"
    assert run("transition", "restore", str(backup_dir), "--target", str(target)) == 1
    assert not target.exists() or not list(target.iterdir())


def test_restore_live_requires_confirm_and_maintenance(populated, backup_dir):
    live = str(populated)
    before = paths.facts_path().read_bytes()
    paths.facts_path().write_text("[]")
    # not in maintenance
    assert run("transition", "restore", str(backup_dir), "--target", live,
               "--confirm-live") == 2
    maintenance.enable("t")
    # no --confirm-live
    assert run("transition", "restore", str(backup_dir), "--target", live) == 2
    assert paths.facts_path().read_text() == "[]"
    assert run("transition", "restore", str(backup_dir), "--target", live,
               "--confirm-live") == 0
    assert paths.facts_path().read_bytes() == before


def test_live_restore_failure_leaves_live_unchanged(populated, backup_dir):
    maintenance.enable("t")
    paths.facts_path().write_text("[1]")
    (backup_dir / "facts.json").write_text("tamper")
    assert run("transition", "restore", str(backup_dir), "--target", str(populated),
               "--confirm-live") == 1
    assert paths.facts_path().read_text() == "[1]"


# ── idle marker / wait-idle ──────────────────────────────────────────────────

def test_backup_refuses_while_sync_in_flight(populated):
    maintenance.enable("t")
    maintenance.mark_sync_running()
    assert run("transition", "backup") == 2
    assert not (populated / "backups").exists()


def test_backup_ignores_stale_marker(populated):
    maintenance.enable("t")
    marker = maintenance.mark_sync_running()
    os.utime(marker, (1, 1))
    assert run("transition", "backup") == 0


def test_backup_wait_idle_waits_for_marker(populated):
    maintenance.enable("t")
    marker = maintenance.mark_sync_running()
    threading.Timer(0.6, maintenance.clear_sync_running, args=(marker,)).start()
    t0 = time.monotonic()
    assert run("transition", "backup", "--wait-idle", "5") == 0
    assert time.monotonic() - t0 >= 0.5


def test_backup_wait_idle_times_out(populated):
    maintenance.enable("t")
    maintenance.mark_sync_running()
    assert run("transition", "backup", "--wait-idle", "1") == 2


def test_maintenance_on_warns_when_sync_running(populated, capsys):
    maintenance.mark_sync_running()
    assert run("transition", "maintenance", "on") == 0
    assert "sync" in capsys.readouterr().err.lower()


# ── backup integrity ─────────────────────────────────────────────────────────

def test_backup_runs_integrity_check(populated, tmp_path):
    from school_dashboard import backup
    maintenance.enable("t")
    with patch.object(backup, "_integrity_result", return_value="*** in database ***"):
        assert run("transition", "backup", "--dest", str(tmp_path / "b")) == 1
    assert not (tmp_path / "b").exists()


def test_backup_rejects_symlinked_source(populated, tmp_path):
    maintenance.enable("t")
    real = tmp_path / "real-facts.json"
    real.write_text("[]")
    paths.facts_path().unlink()
    paths.facts_path().symlink_to(real)
    assert run("transition", "backup", "--dest", str(tmp_path / "b")) == 1
    assert not (tmp_path / "b").exists()


def test_backup_state_json_not_a_dict_is_clean_error(populated, tmp_path, capsys):
    maintenance.enable("t")
    paths.state_path().write_text("[]")
    assert run("transition", "backup", "--dest", str(tmp_path / "b")) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_backup_state_json_garbage_is_clean_error(populated, tmp_path):
    maintenance.enable("t")
    paths.state_path().write_text("{" * 100000)
    assert run("transition", "backup", "--dest", str(tmp_path / "b")) == 1


def test_manifest_hash_is_of_staged_copy(populated, tmp_path):
    from school_dashboard import backup
    maintenance.enable("t")
    real = backup._sha256
    mutate = {"done": False}

    # source mutated after being read must not break the manifest/hash match
    orig_stage = backup._stage_file

    def stage(name, src, staging):
        orig_stage(name, src, staging)
        if name == "facts.json" and not mutate["done"]:
            mutate["done"] = True
            src.write_text("[\"changed after copy\"]")
    with patch.object(backup, "_stage_file", stage):
        assert run("transition", "backup", "--dest", str(tmp_path / "b")) == 0
    m = json.loads((tmp_path / "b" / "manifest.json").read_text())
    entry = next(f for f in m["files"] if f["name"] == "facts.json")
    assert entry["sha256"] == real(tmp_path / "b" / "facts.json")
    assert run("transition", "restore", str(tmp_path / "b"), "--target", str(tmp_path / "t")) == 0


def test_oversized_manifest_rejected(backup_dir, tmp_path):
    (backup_dir / "manifest.json").write_text(" " * (2 * 1024 * 1024))
    assert run("transition", "restore", str(backup_dir), "--target", str(tmp_path / "t")) == 1


def test_created_parents_are_private(populated, tmp_path):
    maintenance.enable("t")
    dest = tmp_path / "a" / "b" / "c"
    assert run("transition", "backup", "--dest", str(dest)) == 0
    for d in (tmp_path / "a", tmp_path / "a" / "b", dest):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_restore_target_parents_are_private(backup_dir, tmp_path):
    t = tmp_path / "x" / "y" / "z"
    assert run("transition", "restore", str(backup_dir), "--target", str(t)) == 0
    for d in (tmp_path / "x", tmp_path / "x" / "y", t):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_restore_leaves_no_temp_files(backup_dir, tmp_path):
    t = tmp_path / "t"
    assert run("transition", "restore", str(backup_dir), "--target", str(t)) == 0
    assert sorted(p.name for p in t.iterdir()) == ["facts.json", "school-state.json", "school.db"]


# ── restore atomicity ────────────────────────────────────────────────────────

def test_live_restore_removes_stale_wal_and_shm(populated, backup_dir):
    maintenance.enable("t")
    wal, shm = Path(str(paths.db_path()) + "-wal"), Path(str(paths.db_path()) + "-shm")
    wal.write_bytes(b"stale")
    shm.write_bytes(b"stale")
    assert run("transition", "restore", str(backup_dir), "--target", str(populated),
               "--confirm-live") == 0
    assert not wal.exists() and not shm.exists()


def test_live_restore_rolls_back_on_third_file_failure(populated, backup_dir):
    from school_dashboard import backup
    maintenance.enable("t")
    paths.facts_path().write_text("[\"live\"]")
    live_state = paths.state_path().read_bytes() + b" "
    paths.state_path().write_bytes(live_state)
    live_db_items = rows(paths.db_path(), "items")
    db.create_item(str(paths.db_path()), child="Z", title="new live")
    live_db_items = rows(paths.db_path(), "items")
    real = backup._replace_file
    n = {"c": 0}

    def flaky(src, dst):
        n["c"] += 1
        if n["c"] == 3:
            raise OSError("injected")
        real(src, dst)
    with patch.object(backup, "_replace_file", flaky):
        assert run("transition", "restore", str(backup_dir), "--target", str(populated),
                   "--confirm-live") == 1
    assert paths.facts_path().read_text() == '["live"]'
    assert paths.state_path().read_bytes() == live_state
    assert rows(paths.db_path(), "items") == live_db_items


def test_live_restore_snapshots_previous_state_privately(populated, backup_dir, capsys):
    maintenance.enable("t")
    paths.facts_path().write_text("[\"before\"]")
    assert run("transition", "restore", str(backup_dir), "--target", str(populated),
               "--confirm-live") == 0
    snaps = [p for p in populated.iterdir() if p.name.startswith(".pre-restore-")]
    assert len(snaps) == 1
    assert stat.S_IMODE(snaps[0].stat().st_mode) == 0o700
    assert (snaps[0] / "facts.json").read_text() == '["before"]'


def test_nonlive_restore_failure_cleans_published_files(backup_dir, tmp_path):
    from school_dashboard import backup
    real = backup._replace_file
    n = {"c": 0}

    def flaky(src, dst):
        n["c"] += 1
        if n["c"] == 2:
            raise OSError("injected")
        real(src, dst)
    target = tmp_path / "t"
    with patch.object(backup, "_replace_file", flaky):
        assert run("transition", "restore", str(backup_dir), "--target", str(target)) == 1
    assert not target.exists() or not list(target.iterdir())


def test_backup_sees_any_of_overlapping_markers(populated):
    maintenance.enable("t")
    a, b = maintenance.mark_sync_running(), maintenance.mark_sync_running()
    maintenance.clear_sync_running(a)
    assert run("transition", "backup") == 2
    maintenance.clear_sync_running(b)
    assert run("transition", "backup") == 0


def test_backup_dest_is_a_file_is_clean_error(populated, tmp_path, capsys):
    maintenance.enable("t")
    f = tmp_path / "afile"
    f.write_text("x")
    assert run("transition", "backup", "--dest", str(f)) == 1
    assert run("transition", "backup", "--dest", str(f / "child")) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_live_restore_rollback_continues_past_a_failing_file(populated, backup_dir, capsys):
    from school_dashboard import backup
    maintenance.enable("t")
    paths.facts_path().write_text('["live"]')
    live_state = paths.state_path().read_bytes() + b" "
    paths.state_path().write_bytes(live_state)
    real = backup._replace_file
    n = {"c": 0}

    def flaky(src, dst):
        n["c"] += 1
        if n["c"] in (3, 4):   # publish of DB fails, then rollback of DB fails
            raise OSError(f"injected {n['c']}")
        real(src, dst)
    with patch.object(backup, "_replace_file", flaky):
        assert run("transition", "restore", str(backup_dir), "--target", str(populated),
                   "--confirm-live") == 1
    err = capsys.readouterr().err.lower()
    assert "rollback failed" in err and "injected 4" in err and "injected 3" in err
    # the remaining files were still rolled back
    assert paths.facts_path().read_text() == '["live"]'
    assert paths.state_path().read_bytes() == live_state
