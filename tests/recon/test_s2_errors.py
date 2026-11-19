"""S2 error paths: bad inputs, backup failure, repo guards."""
import json
import sqlite3

import pytest

from recon.conftest import NOW, load_migration, seed_legacy, table_sha
from recon.support import mk_state
from school_dashboard import maintenance
from school_dashboard.assignments import migrate, migrate_backup, migrate_plan, repo, schema


@pytest.fixture
def env(legacy_env):
    sdir, dbp, statep, _ = legacy_env
    seed_legacy(dbp, statep, load_migration("golden"))
    maintenance.enable("test")
    return sdir, dbp, statep


def test_state_without_action_items_list_is_an_input_error(env):
    env[2].write_text(json.dumps({"action_items": "nope"}))
    with pytest.raises(migrate.MigrationInputError):
        migrate.run(env[1], str(env[2]), dry_run=True, now=NOW)


def test_database_without_items_table_is_an_input_error(tmp_path, env):
    bare = tmp_path / "bare.db"
    sqlite3.connect(bare).close()
    with pytest.raises(migrate.MigrationInputError):
        migrate.run(str(bare), str(env[2]), dry_run=True, now=NOW)


def test_export_of_unmigrated_database_is_an_input_error(env):
    with pytest.raises(migrate.MigrationInputError):
        migrate.export(env[1])


def test_backup_failure_aborts_before_any_write(env, monkeypatch):
    def boom(*a, **k):
        raise migrate_backup.BackupError("x")
    monkeypatch.setattr(migrate_backup, "make_backup", boom)
    before = table_sha(env[1])
    with pytest.raises(migrate.MigrationError):
        migrate.run(env[1], str(env[2]), dry_run=False, now=NOW)
    names = {r[0] for r in sqlite3.connect(env[1]).execute("SELECT name FROM sqlite_master")}
    assert "assignments" not in names and table_sha(env[1]) == before


def test_backup_dir_name_collision_gets_a_suffix(tmp_path):
    db = tmp_path / "a.db"
    sqlite3.connect(db).close()
    st = tmp_path / "s.json"
    st.write_text("{}")
    first = migrate_backup.make_backup(str(db), str(st), tmp_path / "arc", NOW)
    second = migrate_backup.make_backup(str(db), str(st), tmp_path / "arc", NOW)
    assert first != second


def test_plan_tolerates_unparsable_dates_and_keeps_kid_verbatim():
    items = [{"id": 1, "child": "  zed ", "title": "T", "due_date": "garbage", "type": "email",
              "source": "email", "completed": 0, "notes": None, "created_at": "not a date"}]
    plan = migrate_plan.plan_migration(items, [], now=NOW, resolve_kid=lambda n: "")
    rec = plan.records[0]
    assert (rec.state.kid, rec.state.due_date, rec.state.stage_since, rec.state.kind) == ("zed", None, NOW, "task")


def test_update_assignment_without_id_is_rejected(conn):
    with pytest.raises(ValueError):
        repo.update_assignment(conn, mk_state(id=None), now=NOW)


def test_mark_push_rejects_unknown_status(conn):
    with pytest.raises(ValueError):
        repo.mark_push(conn, 1, "weird", now=NOW)


def test_transaction_reraises_non_lock_operational_errors(conn):
    conn.execute("BEGIN")
    try:
        with pytest.raises(sqlite3.OperationalError):
            with repo.transaction(conn):
                pass
    finally:
        conn.execute("ROLLBACK")


def test_ensure_schema_joins_an_open_transaction_and_rolls_back_with_it(db_path):
    c = schema.connect(db_path)
    c.execute("BEGIN IMMEDIATE")
    schema.ensure_schema(c)
    c.execute("ROLLBACK")
    assert "assignments" not in {r[0] for r in c.execute("SELECT name FROM sqlite_master")}


def test_ensure_schema_failure_rolls_back_and_reraises(db_path, monkeypatch):
    c = schema.connect(db_path)
    monkeypatch.setitem(schema._MIGRATIONS, 1, lambda conn: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
    with pytest.raises(sqlite3.OperationalError):
        schema.ensure_schema(c)
    assert not c.in_transaction


def test_readonly_connection_to_wal_database_forbids_writes(conn, db_path):
    ro = schema.connect(db_path, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO recon_meta (key, value) VALUES ('a', 'b')")
