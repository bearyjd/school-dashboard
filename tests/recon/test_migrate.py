"""migrate.py (S2): items + state action_items -> assignments. Plan 3.7 + 10.8."""
import copy
import json
import os
import sqlite3
import stat
from datetime import datetime
from pathlib import Path

import pytest

from recon.conftest import NOW, load_migration, seed_legacy, table_sha
from recon.support import mk_state
from school_dashboard import maintenance
from school_dashboard.assignments import migrate, repo, schema
from school_dashboard.assignments.flags import compute_flag


@pytest.fixture
def env(legacy_env):
    sdir, dbp, statep, _ = legacy_env
    seed_legacy(dbp, statep, load_migration("golden"))
    maintenance.enable("test")
    return sdir, dbp, statep


def run(env, **kw):
    sdir, dbp, statep = env
    kw.setdefault("dry_run", False)
    return migrate.run(dbp, str(statep), now=NOW, **kw)


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def ro(dbp):
    return schema.connect(dbp, readonly=True)


def by_item(dbp, item_id):
    c = ro(dbp)
    row = c.execute("SELECT id FROM assignments WHERE legacy_item_id=?", (item_id,)).fetchone()
    return repo.get_assignment(c, row["id"]), c


# ---- plan_migration (pure) ----
def test_plan_migration_does_not_mutate_its_inputs():
    g = load_migration("golden")
    items, acts = copy.deepcopy(g["items"]), copy.deepcopy(g["action_items"])
    migrate.plan_migration(items, acts, now=NOW, resolve_kid=str.title)
    assert (items, acts) == (g["items"], g["action_items"])


def test_plan_migration_is_deterministic():
    g = load_migration("golden")
    a = migrate.plan_migration(g["items"], g["action_items"], now=NOW, resolve_kid=str.title)
    b = migrate.plan_migration(g["items"], g["action_items"], now=NOW, resolve_kid=str.title)
    assert a == b


# ---- counts and invariants ----
def test_report_counts_rows_in_created_and_merged(env):
    r = run(env)
    assert (r.rows_in, r.created, len(r.merged)) == (13, 12, 1)


def test_rows_in_equals_created_plus_merged_plus_already_migrated(env):
    r = run(env)
    assert r.rows_in == r.created + len(r.merged) + r.already_migrated


def test_merge_is_listed_with_both_sides(env):
    text = json.dumps(list(run(env).merged), default=str)
    assert "item:1" in text and "legacy-action:a1" in text


def test_report_is_not_a_dry_run(env):
    assert run(env).dry_run is False


def test_per_kid_counts_cover_every_kid_including_off_roster(env):
    assert set(run(env).per_kid) == {"Alex", "Sam", "Jo", "Zed"}


def test_off_roster_kid_is_kept_verbatim_with_a_warning(env):
    r = run(env)
    a, _ = by_item(env[1], 6)
    assert a.kid == "Zed" and len(r.warnings) >= 1


def test_items_never_merge_with_other_items(env):
    run(env)
    n = ro(env[1]).execute("SELECT COUNT(*) FROM assignments WHERE legacy_item_id IN (8, 9)").fetchone()[0]
    assert n == 2


def test_action_item_matching_two_planned_records_is_not_merged(env):
    run(env)
    c = ro(env[1])
    assert c.execute("SELECT COUNT(*) FROM assignments WHERE kid='Alex' AND title='Same'").fetchone()[0] == 3
    assert c.execute("SELECT legacy_action_id FROM assignments WHERE legacy_action_id='a4'").fetchone() is not None


def test_merged_action_item_shares_the_record_of_its_item(env):
    run(env)
    a, c = by_item(env[1], 1)
    keys = {l.external_key for l in repo.links_for(c, a.id)}
    assert keys == {"item:1", "legacy-action:a1"}


def test_merged_record_carries_both_legacy_ids(env):
    run(env)
    row = ro(env[1]).execute("SELECT legacy_item_id, legacy_action_id FROM assignments WHERE legacy_item_id=1").fetchone()
    assert (row["legacy_item_id"], row["legacy_action_id"]) == (1, "a1")


# ---- row mapping ----
@pytest.mark.parametrize("item_id,kind", [(1, "assignment"), (3, "task"), (5, "task"), (7, "task")])
def test_item_type_maps_to_kind(env, item_id, kind):
    run(env)
    assert by_item(env[1], item_id)[0].kind == kind


def test_open_item_becomes_stage_1_since_created_at(env):
    run(env)
    a, _ = by_item(env[1], 1)
    assert (a.stage, a.stage_since.replace(tzinfo=None)) == (1, datetime(2026, 11, 10, 8, 0))


def test_completed_item_becomes_stage_4_with_migration_evidence(env):
    run(env)
    a, _ = by_item(env[1], 2)
    assert (a.stage, a.received_evidence) == (4, "migration")


def test_completed_item_closed_at_is_its_completed_at(env):
    run(env)
    a, _ = by_item(env[1], 2)
    assert a.closed_at.replace(tzinfo=None) == datetime(2026, 10, 1, 15, 0)


def test_completed_item_without_completed_at_falls_back_to_created_at(env):
    run(env)
    a, _ = by_item(env[1], 4)
    assert a.closed_at.replace(tzinfo=None) == datetime(2026, 10, 5, 8, 0)


def test_completed_item_gets_a_migration_stage_event_at_the_original_time(env):
    run(env)
    a, c = by_item(env[1], 2)
    ev = [e for e in repo.events_for(c, a.id) if e["kind"] == "stage"]
    assert [(e["setter"], datetime.fromisoformat(e["at"]).replace(tzinfo=None)) for e in ev] == [("migration", datetime(2026, 10, 1, 15, 0))]


def test_notes_are_copied(env):
    run(env)
    row = ro(env[1]).execute("SELECT notes FROM assignments WHERE legacy_item_id=1").fetchone()
    assert row["notes"] == "bring book"


def test_item_link_uses_item_source_key_and_full_row_as_raw_json(env):
    run(env)
    a, c = by_item(env[1], 7)
    row = c.execute("SELECT source, external_key, raw_json FROM source_links WHERE assignment_id=?", (a.id,)).fetchone()
    assert (row["source"], row["external_key"], json.loads(row["raw_json"])["title"]) == ("email", "item:7", "Field trip form")


def test_completed_action_item_becomes_stage_4_with_unknown_class(env):
    run(env)
    c = ro(env[1])
    row = c.execute("SELECT id, stage, class_key, closed_at FROM assignments WHERE legacy_action_id='a2'").fetchone()
    assert (row["stage"], row["class_key"]) == (4, "")
    assert datetime.fromisoformat(row["closed_at"]).replace(tzinfo=None) == datetime(2026, 10, 4, 10, 0)


def test_action_item_link_uses_its_source_and_legacy_action_key(env):
    run(env)
    row = ro(env[1]).execute(
        "SELECT l.source, l.external_key FROM source_links l JOIN assignments a ON a.id=l.assignment_id"
        " WHERE a.legacy_action_id='a3'").fetchone()
    assert (row["source"], row["external_key"]) == ("schoology", "legacy-action:a3")


def test_migration_sets_migrated_at(env):
    run(env)
    assert repo.get_meta(ro(env[1]), "migrated_at") is not None


# ---- no data loss ----
def test_items_table_is_byte_identical_after_migration(env):
    before = table_sha(env[1])
    run(env)
    assert table_sha(env[1]) == before


def test_report_items_sha_before_equals_after(env):
    r = run(env)
    assert r.items_sha_before == r.items_sha_after and r.items_sha_before


def test_state_json_is_untouched(env):
    before = env[2].read_bytes()
    run(env)
    assert env[2].read_bytes() == before


def test_second_run_creates_nothing_and_counts_already_migrated(env):
    run(env)
    r = run(env)
    assert (r.created, len(r.merged), r.already_migrated) == (0, 0, 13)


def test_second_run_leaves_row_and_event_counts_unchanged(env):
    run(env)
    c = ro(env[1])
    before = [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("assignments", "source_links", "assignment_events")]
    run(env)
    after = [ro(env[1]).execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("assignments", "source_links", "assignment_events")]
    assert after == before


def test_completed_history_leaves_needs_action_empty_for_every_kid(legacy_env):
    sdir, dbp, statep, _ = legacy_env
    seed_legacy(dbp, statep, load_migration("completed_history"))
    maintenance.enable("test")
    migrate.run(dbp, str(statep), dry_run=False, now=NOW)
    c = ro(dbp)
    buckets = []
    for a in repo.list_assignments(c, include_closed=True):
        res = compute_flag(a, repo.links_for(c, a.id), now=NOW, no_school=frozenset())
        buckets.append((a.kid, res.bucket))
    assert buckets and {b for _, b in buckets} == {None}


# ---- dry run ----
def test_dry_run_reports_same_counts_without_maintenance(env):
    maintenance.disable()
    r = run(env, dry_run=True)
    assert (r.dry_run, r.rows_in, r.created, len(r.merged)) == (True, 13, 12, 1)


def test_dry_run_writes_nothing_anywhere(env):
    before = snapshot(env[0])
    maintenance.disable()
    before = snapshot(env[0])
    run(env, dry_run=True)
    assert snapshot(env[0]) == before


def test_dry_run_creates_no_v2_tables(env):
    run(env, dry_run=True)
    names = {r[0] for r in sqlite3.connect(env[1]).execute("SELECT name FROM sqlite_master")}
    assert "assignments" not in names


def test_dry_run_after_real_run_reports_everything_already_migrated(env):
    run(env)
    r = run(env, dry_run=True)
    assert (r.created, r.already_migrated) == (0, 13)


def test_dry_run_has_no_backup_dir(env):
    r = run(env, dry_run=True)
    assert r.backup_dir is None and not list((env[0] / "archive").glob("pre-v2-*"))


# ---- refusals ----
def test_real_run_refused_without_maintenance_mode(env):
    maintenance.disable()
    with pytest.raises(migrate.MigrationRefused):
        run(env)


def test_refused_run_writes_nothing(env):
    maintenance.disable()
    before = snapshot(env[0])
    with pytest.raises(migrate.MigrationRefused):
        run(env)
    assert snapshot(env[0]) == before


def test_real_run_refused_while_a_sync_is_in_flight(env):
    marker = maintenance.mark_sync_running()
    try:
        with pytest.raises(migrate.MigrationRefused):
            run(env)
    finally:
        maintenance.clear_sync_running(marker)


def test_refusal_and_error_types_are_runtime_errors():
    assert issubclass(migrate.MigrationRefused, RuntimeError) and issubclass(migrate.MigrationError, RuntimeError)


# ---- backup ----
def _backup_dir(env):
    dirs = list((env[0] / "archive").glob("pre-v2-*"))
    assert len(dirs) == 1
    return dirs[0]


def test_backup_dir_is_created_under_archive_and_reported(env):
    r = run(env)
    assert Path(r.backup_dir) == _backup_dir(env)


def test_backup_dir_is_0700_and_files_0600(env):
    run(env)
    d = _backup_dir(env)
    modes = {stat.S_IMODE(p.stat().st_mode) for p in d.iterdir()}
    assert stat.S_IMODE(d.stat().st_mode) == 0o700 and modes == {0o600}


def test_backup_database_copy_passes_integrity_check(env):
    run(env)
    copy_ = next(_backup_dir(env).glob("*.db"))
    assert sqlite3.connect(copy_).execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_state_copy_is_byte_identical(env):
    run(env)
    copy_ = next(_backup_dir(env).glob("*.json"))
    assert copy_.read_bytes() == env[2].read_bytes()


def test_backup_includes_rows_still_sitting_in_the_wal(env):
    """A raw file copy of a live WAL database would miss these rows; sqlite backup() does not."""
    live = sqlite3.connect(env[1])
    live.execute("PRAGMA journal_mode=WAL"); live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("INSERT INTO items (id, child, title, type, source, completed, created_at)"
                 " VALUES (99, 'Alex', 'wal row', 'assignment', 'manual', 0, '2026-11-18 09:00:00')")
    live.commit()
    try:
        run(env)
        copy_ = next(_backup_dir(env).glob("*.db"))
        assert sqlite3.connect(copy_).execute("SELECT COUNT(*) FROM items WHERE id=99").fetchone()[0] == 1
    finally:
        live.close()


# ---- rollback ----
def _trap(env, sql):
    c = schema.connect(env[1]); schema.ensure_schema(c); c.execute(sql); c.close()


@pytest.mark.parametrize("sql", [
    "CREATE TRIGGER t BEFORE INSERT ON assignments WHEN NEW.title='Quiz' BEGIN SELECT RAISE(ABORT,'injected'); END",
    "CREATE TRIGGER t BEFORE INSERT ON recon_meta WHEN NEW.key='migrated_at' BEGIN SELECT RAISE(ABORT,'injected'); END",
], ids=["mid-run", "last-step"])
def test_failure_during_migration_leaves_no_v2_rows(env, sql):
    _trap(env, sql)
    with pytest.raises(Exception):
        run(env)
    c = ro(env[1])
    counts = [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("assignments", "source_links", "assignment_events")]
    assert counts == [0, 0, 0] and repo.get_meta(c, "migrated_at") is None


def test_failure_during_migration_leaves_items_untouched(env):
    before = table_sha(env[1])
    _trap(env, "CREATE TRIGGER t BEFORE INSERT ON assignments WHEN NEW.title='Quiz' BEGIN SELECT RAISE(ABORT,'x'); END")
    with pytest.raises(Exception):
        run(env)
    assert table_sha(env[1]) == before


def test_silently_dropped_insert_is_caught_by_verification(env):
    _trap(env, "CREATE TRIGGER t BEFORE INSERT ON assignments WHEN NEW.title='Spelling list' BEGIN SELECT RAISE(IGNORE); END")
    with pytest.raises(migrate.MigrationError):
        run(env)
    assert ro(env[1]).execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 0


# ---- export ----
def test_export_contains_assignments_links_events_and_meta(env):
    run(env)
    assert {"assignments", "source_links", "events", "meta"} <= set(migrate.export(env[1]))


def test_export_round_trips_through_json(env):
    run(env)
    data = migrate.export(env[1])
    assert json.loads(json.dumps(data)) == json.loads(json.dumps(data, sort_keys=True))


def test_export_lists_every_migrated_assignment_with_legacy_ids(env):
    run(env)
    data = migrate.export(env[1])
    assert len(data["assignments"]) == 12 and {1, 2, 9} <= {a.get("legacy_item_id") for a in data["assignments"]}


def test_export_includes_event_history(env):
    run(env)
    assert any(e["setter"] == "migration" for e in migrate.export(env[1])["events"])


def test_export_writes_nothing(env):
    run(env)
    before = snapshot(env[0])
    migrate.export(env[1])
    assert snapshot(env[0]) == before
