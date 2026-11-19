"""Review round 1: migration guards pinned, in-transaction gate re-check, inert untrusted scrapes (S1/S2)."""
import argparse
import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

from recon.conftest import NOW, load_migration, seed_legacy, table_sha
from school_dashboard import maintenance
from school_dashboard.assignments import cli, migrate, migrate_backup, migrate_plan, repo, schema


@pytest.fixture
def env(legacy_env):
    sdir, dbp, statep, _ = legacy_env
    seed_legacy(dbp, statep, load_migration("golden"))
    maintenance.enable("test")
    return sdir, dbp, statep


def run(env, **kw):
    kw.setdefault("dry_run", False)
    return migrate.run(env[1], str(env[2]), now=NOW, **kw)


def assert_no_v2(dbp):
    c = sqlite3.connect(dbp)
    try:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        if "assignments" in names:
            assert c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 0
            assert c.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 0
        if "recon_meta" in names:
            assert c.execute("SELECT COUNT(*) FROM recon_meta WHERE key='migrated_at'").fetchone()[0] == 0
    finally:
        c.close()


def after_backup(monkeypatch, action):
    real = migrate_backup.make_backup

    def wrapper(*a, **k):
        out = real(*a, **k)
        action()
        return out
    monkeypatch.setattr(migrate_backup, "make_backup", wrapper)


def wrap_write(monkeypatch, fn):
    real = migrate._write_record

    def wrapper(conn, rec, now):
        fn(real, conn, rec, now)
    monkeypatch.setattr(migrate, "_write_record", wrapper)


# ---- A: guards ----
def test_extra_assignment_row_fails_the_row_count_check(env, monkeypatch):
    def fn(real, conn, rec, now):
        real(conn, rec, now)
        if rec.legacy_item_id == 1:
            conn.execute("INSERT INTO assignments (kid, title, title_key, kind, stage, stage_since, created_at, updated_at)"
                         " SELECT kid, 'extra', 'extra', kind, stage, stage_since, created_at, updated_at"
                         " FROM assignments LIMIT 1")
    wrap_write(monkeypatch, fn)
    with pytest.raises(migrate.MigrationError, match="row counts"):
        run(env)
    assert_no_v2(env[1])


def test_missing_legacy_link_key_fails_the_key_check(env, monkeypatch):
    def fn(real, conn, rec, now):
        if rec.legacy_item_id == 1:
            link = dataclasses.replace(rec.links[0], external_key="bogus:1")
            rec = dataclasses.replace(rec, links=(link,) + rec.links[1:])
        real(conn, rec, now)
    wrap_write(monkeypatch, fn)
    with pytest.raises(migrate.MigrationError, match="no assignment link"):
        run(env)
    assert_no_v2(env[1])


def test_items_changed_during_apply_fails_the_after_write_sha(env, monkeypatch):
    def fn(real, conn, rec, now):
        real(conn, rec, now)
        conn.execute("UPDATE items SET notes='tampered' WHERE id = (SELECT MIN(id) FROM items)")
    wrap_write(monkeypatch, fn)
    with pytest.raises(migrate.MigrationError, match="during migration"):
        run(env)
    assert_no_v2(env[1])


def test_items_changed_between_plan_and_apply_fails_the_before_write_sha(env, monkeypatch):
    def tamper():
        c = sqlite3.connect(env[1])
        with c:
            c.execute("UPDATE items SET notes='tampered' WHERE id = (SELECT MIN(id) FROM items)")
        c.close()
    after_backup(monkeypatch, tamper)
    with pytest.raises(migrate.MigrationError, match="since the plan"):
        run(env)
    assert_no_v2(env[1])


def test_failed_backup_integrity_check_aborts_the_migration(env, monkeypatch):
    monkeypatch.setattr(migrate_backup, "_integrity_result", lambda conn: "*** in database main ***")
    with pytest.raises(migrate.MigrationError, match="integrity"):
        run(env)
    assert_no_v2(env[1])


def test_state_copy_that_differs_aborts_the_migration(env, monkeypatch):
    real = migrate_backup.write_atomic
    monkeypatch.setattr(migrate_backup, "write_atomic", lambda dest, data, mode: real(dest, data + b" ", mode))
    with pytest.raises(migrate.MigrationError, match="byte-identical"):
        run(env)
    assert_no_v2(env[1])


# ---- C, E, I: planning ----
def _item(**kw):
    return {"id": 1, "child": "Alex", "title": "Read ch 3", "due_date": None, "type": "assignment",
            "source": "manual", "completed": 0, "completed_at": None, "notes": None,
            "created_at": "2026-11-10 09:00:00", **kw}


def _act(**kw):
    return {"id": "a1", "child": "Alex", "summary": "Read ch 3", "due": None, "type": "assignment",
            "source": "schoology", "status": "pending", "created": "2026-11-11T09:00:00", **kw}


def plan(items, acts):
    return migrate_plan.plan_migration(items, acts, now=NOW, resolve_kid=lambda n: n)


def test_undated_action_and_undated_item_with_same_kid_and_title_stay_separate():
    p = plan([_item()], [_act()])
    assert (len(p.records), len(p.merged)) == (2, 0)


def test_unreadable_timestamps_are_counted_in_warnings_without_dropping_rows():
    items = [_item(id=1, created_at="junk", title="Secret title A"),
             _item(id=2, completed=1, completed_at="nope", title="Secret title B"),
             _item(id=3, completed=1, completed_at="nope", due_date="31/12/2026", title="Secret title C")]
    p = plan(items, [_act(id="a9", due="bad", created="junk", title="x")])
    assert len(p.records) == 4
    text = " | ".join(p.warnings)
    assert "2 rows had an unreadable created_at" in text
    assert "2 rows had an unreadable completed_at" in text
    assert "2 rows had an unreadable due" in text
    assert "Secret" not in text and "Alex" not in text


def test_readable_or_empty_timestamps_add_no_warnings():
    assert plan([_item(completed=1, completed_at=None, due_date="2026-12-01")], [_act()]).warnings == ()


def test_unreadable_timestamp_warning_reaches_the_report(env):
    c = sqlite3.connect(env[1])
    with c:
        c.execute("UPDATE items SET created_at='junk' WHERE id=1")
    c.close()
    assert any("unreadable created_at" in w for w in run(env, dry_run=True).warnings)


def test_completed_action_merged_into_open_item_closes_the_record():
    p = plan([_item(due_date="2026-11-20")],
             [_act(due="2026-11-20", status="completed", completed_at="2026-11-12T10:00:00")])
    (rec,) = p.records
    assert len(p.merged) == 1
    assert (rec.state.stage, rec.state.received_evidence) == (4, "migration")
    assert rec.state.closed_at is not None and rec.state.closed_at.isoformat().startswith("2026-11-12T10:00:00")
    assert rec.state.stage_since == rec.state.closed_at


def test_open_action_merged_into_completed_item_stays_closed_at_the_items_time():
    p = plan([_item(due_date="2026-11-20", completed=1, completed_at="2026-11-12 08:00:00")],
             [_act(due="2026-11-20")])
    (rec,) = p.records
    assert rec.state.closed_at is not None
    assert (rec.state.stage, rec.state.closed_at.isoformat()[:19]) == (4, "2026-11-12T08:00:00")


def test_merged_open_pair_stays_open():
    (rec,) = plan([_item(due_date="2026-11-20")], [_act(due="2026-11-20")]).records
    assert (rec.state.stage, rec.state.closed_at) == (1, None)


def test_closed_merge_writes_the_stage_event(env, tmp_path):
    c = sqlite3.connect(env[1])
    with c:
        c.execute("DELETE FROM items")
        c.execute("INSERT INTO items (id, child, title, due_date, type, source, completed, created_at)"
                  " VALUES (1, 'Alex', 'Read ch 3', '2026-11-20', 'assignment', 'manual', 0, '2026-11-10 09:00:00')")
    c.close()
    env[2].write_text(json.dumps({"action_items": [_act(due="2026-11-20", status="completed",
                                                         completed_at="2026-11-12T10:00:00")]}))
    run(env)
    ro = schema.connect(env[1], readonly=True)
    assert ro.execute("SELECT COUNT(*) FROM assignment_events WHERE kind='stage' AND setter='migration'"
                      ).fetchone()[0] == 1


# ---- D: invariant ----
def test_rows_in_invariant_violation_raises(env, monkeypatch):
    real = migrate.plan_migration
    monkeypatch.setattr(migrate, "plan_migration",
                        lambda *a, **k: dataclasses.replace(p := real(*a, **k), records=p.records[:-1]))
    with pytest.raises(migrate.MigrationError, match="invariant"):
        run(env, dry_run=True)
    with pytest.raises(migrate.MigrationError, match="invariant"):
        run(env)
    assert_no_v2(env[1])


def test_already_migrated_is_counted_from_the_database_on_a_second_run(env):
    first = run(env)
    second = run(env)
    assert second.already_migrated == first.rows_in and second.created == 0


# ---- F: URI ----
@pytest.fixture
def odd_db(tmp_path):
    d = tmp_path / "we ird?#%25 dir"
    d.mkdir()
    p = d / "odd?name#1%.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE t (x)")
    c.execute("INSERT INTO t VALUES (7)")
    c.commit()
    c.close()
    return p


def test_readonly_connect_handles_special_characters_in_the_path(odd_db):
    c = schema.connect(str(odd_db), readonly=True)
    assert c.execute("SELECT x FROM t").fetchone()[0] == 7
    with pytest.raises(sqlite3.OperationalError):
        c.execute("INSERT INTO t VALUES (8)")


def test_backup_handles_special_characters_in_the_database_path(odd_db, tmp_path):
    st = tmp_path / "s.json"
    st.write_text("{}")
    out = Path(migrate_backup.make_backup(str(odd_db), str(st), tmp_path / "arc", NOW))
    assert sqlite3.connect(out / "school.db").execute("SELECT x FROM t").fetchone()[0] == 7


# ---- G: gate and input hardening ----
def test_connect_failure_in_apply_becomes_a_migration_error(env, monkeypatch):
    real = schema.connect

    def connect(path, *, readonly=False):
        if not readonly:
            raise sqlite3.OperationalError("disk I/O error")
        return real(path, readonly=readonly)
    monkeypatch.setattr(schema, "connect", connect)
    with pytest.raises(migrate.MigrationError):
        run(env)


def test_maintenance_switched_off_after_planning_is_refused_in_the_transaction(env, monkeypatch):
    after_backup(monkeypatch, maintenance.disable)
    with pytest.raises(migrate.MigrationRefused):
        run(env)
    assert_no_v2(env[1])


def test_sync_started_after_planning_is_refused_in_the_transaction(env, monkeypatch):
    markers = []
    after_backup(monkeypatch, lambda: markers.append(maintenance.mark_sync_running()))
    try:
        with pytest.raises(migrate.MigrationRefused):
            run(env)
    finally:
        for m in markers:
            maintenance.clear_sync_running(m)
    assert_no_v2(env[1])


def test_state_file_changed_after_planning_is_rolled_back(env, monkeypatch):
    after_backup(monkeypatch, lambda: env[2].write_bytes(env[2].read_bytes() + b"\n"))
    with pytest.raises(migrate.MigrationError, match="state file changed"):
        run(env)
    assert_no_v2(env[1])


def test_deeply_nested_state_json_is_an_input_error(env):
    env[2].write_text("[" * 200000)
    with pytest.raises(migrate.MigrationInputError):
        run(env, dry_run=True)


def test_oversized_state_file_is_an_input_error(env, monkeypatch):
    monkeypatch.setattr(migrate, "MAX_STATE_BYTES", 10)
    with pytest.raises(migrate.MigrationInputError, match="too large"):
        run(env, dry_run=True)
    assert migrate.MAX_STATE_BYTES == 10


def test_state_size_limit_constant_is_50_mib():
    assert migrate.MAX_STATE_BYTES == 50 * 1024 * 1024


# ---- J: CLI ----
def test_oserror_reaching_the_cli_prints_only_the_exception_type(env, monkeypatch, capsys):
    def boom(*a, **k):
        raise PermissionError(13, "denied", "/secret/path/school.db")
    monkeypatch.setattr(migrate, "run", boom)
    ns = argparse.Namespace(assignments_command="migrate", state_file=None, dry_run=True, json=False)
    with pytest.raises(SystemExit) as ei:
        cli.cmd_assignments(ns)
    err = capsys.readouterr().err
    assert ei.value.code == 1 and "PermissionError" in err and "/secret" not in err and "denied" not in err
