"""`school-state assignments migrate|export`: exit codes, JSON, quiet stderr."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from recon.conftest import load_migration, seed_legacy, table_sha
from school_dashboard import maintenance
from school_dashboard.assignments import schema

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(legacy_env):
    sdir, dbp, statep, cfg = legacy_env
    seed_legacy(dbp, statep, load_migration("golden"))
    return sdir, dbp, statep, cfg


def cli(env, *args):
    sdir, _, _, cfg = env
    e = {**os.environ, "SCHOOL_STATE_DIR": str(sdir), "PYTHONPATH": str(REPO), "SCHOOL_DASHBOARD_CONFIG": str(cfg)}
    for k in ("SCHOOL_STATE_PATH", "SCHOOL_DB_PATH"):
        e.pop(k, None)
    return subprocess.run([sys.executable, "-m", "school_dashboard.cli", "assignments", *args],
                          capture_output=True, text=True, env=e, cwd=REPO)


def test_dry_run_json_works_without_maintenance(env):
    r = cli(env, "migrate", "--dry-run", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["dry_run"] is True


def test_dry_run_json_reports_row_counts(env):
    out = json.loads(cli(env, "migrate", "--dry-run", "--json").stdout)
    assert (out["rows_in"], out["created"]) == (13, 12)


def test_real_run_without_maintenance_exits_3(env):
    assert cli(env, "migrate", "--json").returncode == 3


def test_real_run_while_sync_in_flight_exits_3(env):
    maintenance.enable("test")
    marker = maintenance.mark_sync_running()
    try:
        assert cli(env, "migrate").returncode == 3
    finally:
        maintenance.clear_sync_running(marker)


def test_real_run_with_maintenance_exits_0_and_prints_report_json(env):
    maintenance.enable("test")
    r = cli(env, "migrate", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["created"] == 12


def test_second_real_run_exits_0_and_creates_nothing(env):
    maintenance.enable("test")
    cli(env, "migrate", "--json")
    out = json.loads(cli(env, "migrate", "--json").stdout)
    assert (out["created"], out["already_migrated"]) == (0, 13)


def test_missing_database_exits_2(env):
    Path(env[1]).unlink()
    assert cli(env, "migrate", "--dry-run").returncode == 2


def test_malformed_state_json_exits_2(env):
    env[2].write_text("{not json")
    assert cli(env, "migrate", "--dry-run").returncode == 2


def test_missing_state_file_exits_2(env):
    env[2].unlink()
    assert cli(env, "migrate", "--dry-run").returncode == 2


def test_rolled_back_migration_exits_1_and_leaves_items_alone(env):
    maintenance.enable("test")
    c = schema.connect(env[1]); schema.ensure_schema(c)
    c.execute("CREATE TRIGGER t BEFORE INSERT ON assignments WHEN NEW.title='Quiz' BEGIN SELECT RAISE(ABORT,'x'); END"); c.close()
    before = table_sha(env[1])
    assert cli(env, "migrate").returncode == 1
    assert table_sha(env[1]) == before


def test_stderr_never_contains_titles_or_kid_names(env):
    maintenance.enable("test")
    err = cli(env, "migrate", "--json").stderr
    assert not any(w in err for w in ("Read ch 5", "Old worksheet", "Alex", "Sam", "Jo "))


def test_export_json_exits_0_with_assignments(env):
    maintenance.enable("test")
    cli(env, "migrate")
    r = cli(env, "export", "--json")
    assert r.returncode == 0, r.stderr
    assert len(json.loads(r.stdout)["assignments"]) == 12


def test_export_missing_database_exits_2(env):
    Path(env[1]).unlink()
    assert cli(env, "export", "--json").returncode == 2
