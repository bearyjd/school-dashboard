"""Migration against the REAL legacy shape (real keys, synthetic values): empty summaries, mixed dues, aliases."""
import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from recon.conftest import NOW, load_migration, table_sha
from school_dashboard import maintenance, state
from school_dashboard.assignments import migrate, schema

REPO = Path(__file__).resolve().parents[2]
SENTINEL = "(untitled Schoology item)"


def _seed(dbp: str, statep: Path, data: dict, extra_actions=()) -> None:
    c = sqlite3.connect(dbp)
    with c:
        for it in data["items"]:
            c.execute("INSERT INTO items (id, child, title, due_date, type, source, completed, completed_at, notes,"
                      " created_at) VALUES (:id, :child, :title, :due_date, :type, :source, :completed,"
                      " :completed_at, :notes, :created_at)", it)
    c.close()
    st = {**data["state"], "action_items": [*data["state"]["action_items"], *extra_actions]}
    statep.write_text(json.dumps(st, indent=1))


@pytest.fixture
def make_env(state_env, tmp_path, monkeypatch):
    def build(extra_actions=()):
        data = load_migration("real_shape")
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"children": data["roster"], "name_aliases": data["name_aliases"]}))
        monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
        monkeypatch.setattr(state, "_config_cache", None)
        from school_dashboard import db
        dbp = state_env / "school.db"
        db.init_db(str(dbp))
        _seed(str(dbp), state_env / "school-state.json", data, extra_actions)
        maintenance.enable("test")
        return state_env, str(dbp), state_env / "school-state.json", cfg
    return build


def run(env, **kw):
    kw.setdefault("dry_run", False)
    return migrate.run(env[1], str(env[2]), now=NOW, **kw)


def rows(dbp, sql, *args):
    c = schema.connect(dbp, readonly=True)
    try:
        return [tuple(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def listing(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_fixture_has_the_real_key_set_and_counts():
    acts = load_migration("real_shape")["state"]["action_items"]
    assert len(acts) == 97 and {a["source"] for a in acts} == {"schoology"}
    assert set().union(*(set(a) for a in acts)) == {"id", "child", "source", "type", "summary", "due", "status",
                                                    "created", "last_seen", "missed", "completed_at"}
    assert sum(1 for a in acts if "summary" not in a) == 1


def test_real_run_succeeds_on_the_real_shape(make_env):
    """RED root cause: the action item with no summary became title '' and trg_assignments_title_ins aborted."""
    env = make_env()
    r = run(env)
    assert r.rows_in == 100 and r.skipped == 0
    assert r.rows_in == r.created + len(r.merged) + r.already_migrated + r.skipped


def test_dry_run_and_real_run_both_succeed_with_identical_reports(make_env):
    env = make_env()
    dry = run(env, dry_run=True)
    real = run(env)
    assert dataclasses.replace(real, dry_run=True, backup_dir=None) == dry


def test_every_created_title_is_non_empty_within_300(make_env):
    env = make_env()
    run(env)
    lengths = [r[0] for r in rows(env[1], "SELECT length(title) FROM assignments")]
    assert len(lengths) == 100 and all(1 <= n <= 300 for n in lengths)


def test_title_fallbacks_and_counted_warnings(make_env):
    env = make_env()
    r = run(env)
    titles = dict(rows(env[1], "SELECT legacy_action_id, title FROM assignments WHERE legacy_action_id IS NOT NULL"))
    assert titles["act001"] == titles["act002"] == titles["act003"] == SENTINEL
    assert len(titles["act005"]) == 300 and titles["act005"].endswith("…")
    assert titles["act006"] == "Tabbell title[0m"
    joined = " | ".join(r.warnings)
    assert "had no title; placeholder used" in joined and "title was shortened" in joined or "titles were shortened" in joined
    assert "Synthetic" not in joined and "Alex" not in joined


def test_empty_item_title_gets_the_generic_placeholder(make_env):
    env = make_env()
    run(env)
    assert rows(env[1], "SELECT title FROM assignments WHERE legacy_item_id = 3") == [("(untitled item)",)]


def test_aliased_child_has_the_short_kid_everywhere(make_env):
    env = make_env()
    r = run(env)
    kids = {k for (k,) in rows(env[1], "SELECT DISTINCT kid FROM assignments")}
    assert "Samantha" not in kids and "Sam" in kids and "Samantha" not in r.per_kid
    assert {k for (k,) in rows(env[1], "SELECT DISTINCT kid_external FROM source_links")
            if k.startswith("Samantha")} == set()
    assert not any("Samantha" in w for w in r.warnings)


def test_missing_child_is_kept_under_unknown_with_a_counted_warning(make_env):
    env = make_env()
    r = run(env)
    assert rows(env[1], "SELECT kid FROM assignments WHERE legacy_action_id = 'act007'") == [("(unknown)",)]
    assert any("no usable kid" in w for w in r.warnings)


def test_nothing_is_dropped_and_mixed_dues_are_counted(make_env):
    env = make_env()
    r = run(env)
    assert rows(env[1], "SELECT COUNT(*) FROM source_links")[0][0] == r.created + len(r.merged)
    assert any("unreadable due" in w for w in r.warnings)


def test_second_run_is_a_no_op(make_env):
    env = make_env()
    run(env)
    before = table_sha(env[1], "assignments")
    r = run(env)
    assert (r.created, len(r.merged), r.skipped, r.already_migrated) == (0, 0, 0, 100)
    assert table_sha(env[1], "assignments") == before


def test_rejected_row_is_skipped_in_dry_run_and_real_run_alike(make_env):
    bad = {"id": "bad001", "child": "K" * 100, "source": "schoology", "type": "task", "summary": "Too long a kid",
           "due": None, "status": "pending", "created": "2026-11-01T07:00:00"}
    env = make_env([bad])
    dry = run(env, dry_run=True)
    real = run(env)
    assert real.skipped == 1 and real.skipped_keys == ("legacy-action:bad001",)
    assert any("1 rows could not be migrated and were left in the legacy store" in w for w in real.warnings)
    assert dataclasses.replace(real, dry_run=True, backup_dir=None) == dry
    assert real.rows_in == real.created + len(real.merged) + real.already_migrated + real.skipped
    assert rows(env[1], "SELECT COUNT(*) FROM assignments WHERE legacy_action_id = 'bad001'") == [(0,)]
    assert "Too long" not in json.dumps(dataclasses.asdict(real), default=str)


def test_skipped_row_is_not_counted_as_migrated_and_is_retried(make_env):
    bad = {"id": "bad001", "child": "K" * 100, "source": "schoology", "type": "task", "summary": "x",
           "due": None, "status": "pending", "created": "2026-11-01T07:00:00"}
    env = make_env([bad])
    run(env)
    again = run(env)
    assert (again.created, again.already_migrated, again.skipped) == (0, 100, 1)
    assert again.skipped_keys == ("legacy-action:bad001",)
    assert rows(env[1], "SELECT value FROM recon_meta WHERE key = 'migrated_at'")


def test_dry_run_writes_nothing_and_leaves_no_scratch(make_env, tmp_path):
    env = make_env()
    maintenance.disable()
    before = listing(env[0])
    run(env, dry_run=True)
    assert listing(env[0]) == before
    assert not [p for p in Path(tempfile_dir()).glob("recon-dryrun-*")]


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


def _cli(env, *args):
    sdir, _, _, cfg = env
    e = {**os.environ, "SCHOOL_STATE_DIR": str(sdir), "PYTHONPATH": str(REPO), "SCHOOL_DASHBOARD_CONFIG": str(cfg)}
    for k in ("SCHOOL_STATE_PATH", "SCHOOL_DB_PATH"):
        e.pop(k, None)
    return subprocess.run([sys.executable, "-m", "school_dashboard.cli", "assignments", *args],
                          capture_output=True, text=True, env=e, cwd=REPO)


def test_cli_exit_codes_zero_then_four_for_a_partial_migration(make_env):
    bad = {"id": "bad001", "child": "K" * 100, "source": "schoology", "type": "task", "summary": "x",
           "due": None, "status": "pending", "created": "2026-11-01T07:00:00"}
    env = make_env([bad])
    r = _cli(env, "migrate", "--json")
    assert r.returncode == 4, r.stderr
    assert json.loads(r.stdout)["skipped"] == 1


def test_cli_exit_zero_when_everything_migrated(make_env):
    env = make_env()
    r = _cli(env, "migrate", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["skipped"] == 0


def test_slash_dues_are_now_parsed_and_yearless_or_garbage_dues_are_counted(make_env):
    """Fixture dues: 12 x 11/22/2026 now dated; 12 x 'Mon, Nov 16' (no year) + 12 x 'not a date' + 1 item
    'garbage' stay undated and are counted (25 unreadable)."""
    env = make_env()
    r = run(env)
    assert rows(env[1], "SELECT COUNT(*) FROM assignments WHERE due_date = '2026-11-22'")[0][0] == 12
    assert "25 rows had an unreadable due; left undated" in r.warnings


def _trap(env, sql):
    c = schema.connect(env[1])
    schema.ensure_schema(c)
    c.execute(sql)
    c.close()


def test_rejected_link_insert_leaves_no_assignment_and_no_event_for_that_row(make_env):
    done = {"id": "cmp001", "child": "Alex", "source": "schoology", "type": "task", "summary": "Closed one",
            "due": None, "status": "completed", "completed_at": "2026-11-05T07:00:00",
            "created": "2026-11-01T07:00:00"}
    env = make_env([done])
    _trap(env, "CREATE TRIGGER t BEFORE INSERT ON source_links WHEN NEW.external_key = 'legacy-action:cmp001' "
               "BEGIN SELECT RAISE(ABORT, 'injected'); END")
    r = run(env)
    assert r.skipped_keys == ("legacy-action:cmp001",)
    assert rows(env[1], "SELECT COUNT(*) FROM assignments WHERE legacy_action_id = 'cmp001'") == [(0,)]
    assert rows(env[1], "SELECT COUNT(*) FROM assignments") == [(100,)]
    assert rows(env[1], "SELECT COUNT(*) FROM assignment_events "
                        "WHERE assignment_id NOT IN (SELECT id FROM assignments)") == [(0,)]
    assert rows(env[1], "SELECT COUNT(*) FROM assignment_events WHERE setter = 'migration'") == [(
        rows(env[1], "SELECT COUNT(*) FROM assignments WHERE stage = 4")[0][0],)]


def test_every_row_rejected_leaves_migrated_at_unset_and_exits_four(make_env):
    env = make_env()
    _trap(env, "CREATE TRIGGER t BEFORE INSERT ON source_links BEGIN SELECT RAISE(ABORT, 'injected'); END")
    r = _cli(env, "migrate", "--json")
    assert r.returncode == 4, r.stderr
    rep = json.loads(r.stdout)
    assert rep["skipped"] == 100 and rep["created"] == 0
    assert rows(env[1], "SELECT COUNT(*) FROM assignments") == [(0,)]
    assert rows(env[1], "SELECT COUNT(*) FROM recon_meta WHERE key = 'migrated_at'") == [(0,)]
