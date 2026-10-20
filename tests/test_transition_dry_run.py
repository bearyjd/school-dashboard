"""transition dry-run: strictly read-only report."""
import hashlib
import json
import os
import socket
import sqlite3
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from school_dashboard import cli, db, dry_run, maintenance, paths, state

SECRET = "SUPERSECRET-TOKEN-VALUE"
TODAY = date(2026, 10, 19)


def run(*argv):
    with patch("sys.argv", ["school-state", "transition", "dry-run", *argv]):
        try:
            cli.main()
        except SystemExit as e:
            return e.code if e.code is not None else 0
    return 0


def snapshot(root):
    """{relpath: (kind, sha256, mtime_ns)} for every entry, recursively."""
    snap = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_dir():
            snap[rel] = ("dir", None, p.stat().st_mtime_ns)
        else:
            snap[rel] = ("file", hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    return snap


@pytest.fixture
def world(state_env, tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "children": {
            "Ford": {"id": "ford", "grade": "7", "school": "MS"},
            "Jack": {"id": "jack", "grade": "4", "school": "ES"},
        },
        "name_aliases": {"jackie": "Jack"},
    }))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.setattr(state, "_config_cache", None)
    monkeypatch.setenv("IXL_DIR", str(tmp_path / "no-ixl"))
    monkeypatch.setenv("SGY_FILE", str(tmp_path / "no-sgy.json"))
    monkeypatch.delenv("GC_TEAM_MAP", raising=False)
    dbp = str(paths.db_path())
    db.init_db(dbp)
    db.create_item(dbp, child="Ford", title="Read ch 3", due_date="2026-10-25", source="manual")
    db.create_item(dbp, child="Ford", title="Old essay", due_date="2026-08-01", source="manual")
    db.create_item(dbp, child="Jack", title="Math", source="manual")
    done = db.create_item(dbp, child="Jack", title="Done thing", source="manual")
    db.complete_item(dbp, done if isinstance(done, int) else 4)
    db.create_item(dbp, child="Stranger", title="Mystery", due_date="2026-10-30", source="manual")
    db.create_digest(dbp, "Morning", [{"t": 1}])
    s = state.load(str(paths.state_path()))
    state.add_action_item(s, "Ford", "schoology", "assignment", "Read Ch 3", "2026-10-25")
    state.add_action_item(s, "Ford", "manual", "task", "Read  ch 3", "2026-10-25")
    s["ixl"]["Ford"] = {"remaining": []}
    s["schoology"]["Jackie"] = {"assignments": []}
    state.save(s, str(paths.state_path()))
    paths.facts_path().write_text('[{"subject": "x"}, {"subject": "y"}]')
    paths.gc_path().write_text(json.dumps({"teams": [
        {"team_id": "t1", "team_name": "Hawks", "child": "", "schedule": [{"date": "2026-11-01"}]},
        {"team_id": "t2", "team_name": "Owls", "child": "", "schedule": []},
    ]}))
    paths.sync_meta_path().write_text(json.dumps({"ixl": {"last_run": "2026-10-19T06:00:00", "last_result": "ok"}}))
    return state_env


def report(**kw):
    return dry_run.build_report("2026-27", from_year="2025-26", today=TODAY,
                                now=datetime(2026, 10, 19, tzinfo=timezone.utc), **kw)


# ── arguments ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["2026", "2026-28", "26-27", "2026-2027", "abcd-ef", "2026-26"])
@pytest.mark.usefixtures("world")
def test_bad_to_year_exit_2(bad):
    assert run("--to-year", bad) == 2


@pytest.mark.usefixtures("world")
def test_missing_to_year_exit_2():
    assert run() == 2


@pytest.mark.usefixtures("world")
def test_bad_from_year_exit_2():
    assert run("--to-year", "2026-27", "--from-year", "2025-27") == 2


@pytest.mark.usefixtures("world")
def test_to_year_must_follow_from_year():
    assert run("--to-year", "2026-27", "--from-year", "2026-27") == 2


def test_default_from_year_aug_start():
    assert dry_run.default_from_year(date(2026, 10, 19)) == "2026-27"
    assert dry_run.default_from_year(date(2026, 7, 31)) == "2025-26"
    assert dry_run.default_from_year(date(2026, 8, 1)) == "2026-27"


# ── report content ───────────────────────────────────────────────────────────

@pytest.mark.usefixtures("world")
def test_report_roster_and_children():
    r = report()
    ford = next(c for c in r["roster"] if c["id"] == "ford")
    assert ford["name"] == "Ford" and ford["grade"] == "7" and ford["school"] == "MS"
    assert ford["ixl_data"] is True and ford["schoology_data"] is False
    jack = next(c for c in r["roster"] if c["id"] == "jack")
    assert jack["schoology_data"] is True  # via alias "Jackie"
    assert "Stranger" in r["unconfigured_children"]


@pytest.mark.usefixtures("world")
def test_report_counts():
    r = report()
    items = r["stores"]["sqlite_items"]
    assert items["open"] == 4 and items["completed"] == 1
    assert items["by_source"]["manual"] == {"open": 4, "completed": 1}
    assert r["stores"]["digests"] == 1
    assert r["stores"]["events"] == {"table_exists": True, "count": 0}
    assert r["stores"]["state_action_items"]["open"] == 2
    assert r["stores"]["facts"] == 2
    assert r["stores"]["gc"] == {"teams": 2, "events": 1}
    assert r["stores"]["sync_meta"]["ixl"]["last_result"] == "ok"
    assert r["stores"]["email_digest_present"] is False


@pytest.mark.usefixtures("world")
def test_carryover_treatments_and_duplicates():
    r = report()
    by_title = {e["title"]: e for e in r["carryover"]["unresolved"]}
    assert by_title["old essay"]["treatment"] == "archive-candidate"
    assert by_title["math"]["treatment"] == "review"
    assert by_title["mystery"]["child_configured"] is False
    ch3 = [e for e in r["carryover"]["unresolved"] if e["title"] == "read ch 3"]
    manual = next(e for e in ch3 if e["source"] == "manual")
    assert manual["treatment"] == "carry"
    assert sorted(manual["stores"]) == ["sqlite", "state"]
    assert "duplicate" in manual["flags"]
    assert r["carryover"]["retain_history"] == 1
    assert "owner" in r["carryover"]["note"].lower()


@pytest.mark.usefixtures("world")
def test_collision_same_title_different_source():
    r = report()
    cols = r["carryover"]["collisions"]
    assert any(c["child_id"] == "ford" and c["title"] == "read ch 3"
               and sorted(c["sources"]) == ["manual", "schoology"] for c in cols)


@pytest.mark.usefixtures("world")
def test_gc_memberships(monkeypatch):
    monkeypatch.setenv("GC_TEAM_MAP", "t1:Ford")
    r = report()
    m = {t["team_id"]: t for t in r["memberships"]}
    assert m["t1"]["status"].startswith("continuing")
    assert m["t1"]["child_id"] == "ford"
    assert m["t2"]["status"] == "unmapped"
    assert any(g["code"] == "gc-team-unmapped" for g in r["gaps"])


@pytest.mark.usefixtures("world")
def test_stale_inputs(tmp_path, monkeypatch):
    ixl = tmp_path / "ixl"
    ixl.mkdir()
    (ixl / "ford-assigned.json").write_text(SECRET)
    sgy = tmp_path / "sgy.json"
    sgy.write_text(SECRET)
    monkeypatch.setenv("IXL_DIR", str(ixl))
    monkeypatch.setenv("SGY_FILE", str(sgy))
    r = report()
    assert r["stale_inputs"]["ixl_dir"]["files"][0]["name"] == "ford-assigned.json"
    assert r["stale_inputs"]["sgy_file"]["present"] is True
    assert "refill" in r["stale_inputs"]["risk"]
    assert SECRET not in json.dumps(r)


@pytest.mark.usefixtures("world")
def test_gaps():
    codes = {g["code"] for g in report()["gaps"]}
    assert {"no-calendar-source", "no-activity-inventory", "pruning-not-suspended",
            "no-recent-backup"} <= codes


def test_recent_backup_clears_gap(world):
    b = world / "backups" / "20261018"
    b.mkdir(parents=True)
    (b / "manifest.json").write_text(json.dumps({"created_at": "2026-10-18T00:00:00+00:00"}))
    r = report()
    assert "no-recent-backup" not in {g["code"] for g in r["gaps"]}
    assert r["backups"]["latest"] == "2026-10-18T00:00:00+00:00"


@pytest.mark.usefixtures("world")
def test_archive_dest_checks(tmp_path):
    r = report()
    assert r["archive"]["dest"].endswith("archive/2025-26-to-2026-27")
    assert r["archive"]["exists"] is False and r["archive"]["valid"] is True
    existing = tmp_path / "exists"
    existing.mkdir()
    r2 = report(archive_dest=existing)
    assert r2["archive"]["valid"] is False
    assert "archive-dest-invalid" in {g["code"] for g in r2["gaps"]}


# ── read-only guarantees ─────────────────────────────────────────────────────

@pytest.mark.parametrize("maint", [False, True])
def test_no_writes_any_maintenance(world, maint, capsys):
    if maint:
        maintenance.enable("test")
    before = snapshot(world)
    listing = sorted(os.listdir(world))
    assert run("--to-year", "2026-27", "--from-year", "2025-26") == 0
    assert run("--to-year", "2026-27", "--from-year", "2025-26", "--json") == 0
    assert snapshot(world) == before
    assert sorted(os.listdir(world)) == listing
    assert not list(world.glob("*-wal")) and not list(world.glob("*-shm"))
    assert maintenance.is_active() is maint


def test_missing_everything_reports_gaps_creates_nothing(state_env, monkeypatch, tmp_path):
    monkeypatch.delenv("SCHOOL_DASHBOARD_CONFIG", raising=False)
    monkeypatch.setenv("IXL_DIR", str(tmp_path / "nx"))
    monkeypatch.setenv("SGY_FILE", str(tmp_path / "nx.json"))
    before = snapshot(state_env)
    r = report()
    assert snapshot(state_env) == before and not list(state_env.iterdir())
    codes = {g["code"] for g in r["gaps"]}
    assert {"db-missing", "state-missing", "roster-empty"} <= codes
    assert run("--to-year", "2026-27", "--from-year", "2025-26") == 0


def test_missing_events_table_is_gap_not_created(state_env, monkeypatch, tmp_path):
    monkeypatch.setenv("IXL_DIR", str(tmp_path / "nx"))
    p = paths.db_path()
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, child TEXT, title TEXT, due_date TEXT,"
                 " type TEXT, source TEXT, completed INTEGER, completed_at TEXT, notes TEXT, created_at TEXT)")
    conn.commit()
    conn.close()
    before = snapshot(state_env)
    r = report()
    assert r["stores"]["events"]["table_exists"] is False
    assert "events-table-missing" in {g["code"] for g in r["gaps"]}
    assert snapshot(state_env) == before
    with sqlite3.connect(p) as c:
        names = {x[0] for x in c.execute("select name from sqlite_master where type='table'")}
    assert names == {"items"}


def test_readonly_db_and_dir(world):
    paths.db_path().chmod(0o444)
    world.chmod(0o555)
    try:
        r = report()
        assert r["stores"]["sqlite_items"]["open"] == 4
        assert run("--to-year", "2026-27", "--from-year", "2025-26") == 0
    finally:
        world.chmod(0o755)
        paths.db_path().chmod(0o644)


@pytest.mark.usefixtures("world")
def test_json_output_has_no_secrets(monkeypatch, capsys):
    monkeypatch.setenv("GC_TOKEN", SECRET)
    monkeypatch.setenv("LITELLM_API_KEY", SECRET)
    assert run("--to-year", "2026-27", "--from-year", "2025-26", "--json") == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["years"] == {"from": "2025-26", "to": "2026-27"}
    assert SECRET not in out
    assert run("--to-year", "2026-27", "--from-year", "2025-26") == 0
    assert SECRET not in capsys.readouterr().out


@pytest.mark.usefixtures("world")
def test_network_blocked(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", boom)
    assert run("--to-year", "2026-27", "--from-year", "2025-26") == 0


@pytest.mark.usefixtures("world")
def test_text_render_mentions_owner_decides(capsys):
    run("--to-year", "2026-27", "--from-year", "2025-26")
    out = capsys.readouterr().out
    assert "2025-26" in out and "2026-27" in out and "family owner" in out.lower()


@pytest.mark.usefixtures("world")
def test_db_opened_with_readonly_uri(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    seen = []
    real = sqlite3.connect

    def spy(target, *a, **k):
        seen.append((str(target), k.get("uri")))
        return real(target, *a, **k)
    monkeypatch.setattr(sqlite3, "connect", spy)
    report()
    assert seen
    for target, uri in seen:
        assert uri is True
        assert parse_qs(urlsplit(target).query)["mode"] == ["ro"]


@pytest.mark.usefixtures("world")
def test_write_on_dry_run_connection_fails():
    with dry_run._open_ro(paths.db_path()) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO items (child, title) VALUES ('a', 'b')")


# ── WAL-mode databases ───────────────────────────────────────────────────────

@pytest.fixture
def wal_db(state_env, tmp_path, monkeypatch):
    monkeypatch.setenv("IXL_DIR", str(tmp_path / "nx"))
    monkeypatch.setenv("SGY_FILE", str(tmp_path / "nx.json"))
    writer = sqlite3.connect(paths.db_path(), isolation_level=None)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, child TEXT, title TEXT,"
                   " due_date TEXT, source TEXT, completed INTEGER DEFAULT 0)")
    writer.execute("INSERT INTO items (child, title, source) VALUES ('Ford', 'in wal', 'manual')")
    yield writer
    writer.close()


def test_wal_rows_counted_and_nothing_touched(state_env, wal_db):
    assert (state_env / "school.db-wal").exists()
    before = snapshot(state_env)
    r = report()
    assert r["stores"]["sqlite_items"]["open"] == 1
    assert snapshot(state_env) == before
    assert not r["gaps"] or "db-unreadable" not in {g["code"] for g in r["gaps"]}


def test_wal_only_main_file_creates_no_sidecars(state_env, wal_db):
    wal_db.close()  # clean close checkpoints and removes -wal/-shm
    assert not (state_env / "school.db-wal").exists()
    before = snapshot(state_env)
    r = report()
    assert r["stores"]["sqlite_items"]["open"] == 1
    assert snapshot(state_env) == before
    assert not list(state_env.glob("school.db-*"))


def test_wal_readonly_state_dir(state_env, wal_db):
    before = snapshot(state_env)
    state_env.chmod(0o555)
    try:
        assert report()["stores"]["sqlite_items"]["open"] == 1
    finally:
        state_env.chmod(0o755)
    assert snapshot(state_env) == before


def test_wal_temp_copy_cleaned_up(state_env, wal_db, monkeypatch):
    import tempfile
    made = []
    real = tempfile.TemporaryDirectory

    def spy(*a, **k):
        t = real(*a, **k)
        made.append(t.name)
        return t
    monkeypatch.setattr(tempfile, "TemporaryDirectory", spy)
    report()
    assert made and not any(os.path.exists(m) for m in made)


def test_mismatch_message_notes_derived_from_year():
    with pytest.raises(dry_run.DryRunArgError, match="derived from today"):
        dry_run.resolve_years("2028-29", None, TODAY)


def test_naive_sync_meta_timestamp_is_local_time(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    import time
    time.tzset()
    try:
        now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)  # 08:00 local
        out = dry_run._sync_meta_report({"ixl": {"last_run": "2026-10-19T06:00:00"}}, now)
        assert out["ixl"]["age_hours"] == 2.0
    finally:
        monkeypatch.undo()
        time.tzset()


def test_db_error_detail_in_gap(state_env, monkeypatch):
    paths.db_path().write_bytes(b"not a database at all, just junk bytes" * 4)
    codes = {g["code"]: g for g in report()["gaps"]}
    assert "db-unreadable" in codes and "DatabaseError" in codes["db-unreadable"]["message"]
