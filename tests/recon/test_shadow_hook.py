"""run_shadow + the one-call hook in `school-state update` (v1 must be unaffected)."""
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from contract_support import build_state, set_roster
from school_dashboard import scrape_meta
from recon.ingest_support import make_db, ns
from recon.support import local

NOW = local(2026, 11, 18, 17)


@pytest.fixture
def env(state_env, tmp_path, monkeypatch):
    for name in ("RECON_INGEST_ENABLED", "RECON_PUSH_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    set_roster(tmp_path, monkeypatch)
    inputs = tmp_path / "inputs"
    (inputs / "ixl").mkdir(parents=True)
    return state_env, inputs


def _write(inputs, sgy=True, ps=True, ps_stamp="2026-11-18T10:00:00-05:00"):
    if sgy:
        (inputs / "sgy.json").write_text(json.dumps({"per_child": [{
            "child": {"name": "Alpha Tester"}, "scrape_confidence": "high",
            "assignments": [{"title": "Worksheet", "course": "Math 6", "due_date": "2026-11-20", "status": "unknown",
                             "link": "https://sgy.example.invalid/a/1"}]}]}))
    if ps:
        (inputs / "ps.json").write_text(json.dumps({"scraped_at": ps_stamp, "children": [{
            "name": "Alpha Tester", "missing": [], "late": [],
            "upcoming": [{"course": "Sci 6", "title": "Lab", "due": "2026-11-20", "score": None}], "warnings": []}]}))


def _args(state_dir, inputs):
    return ns(sgy_file=str(inputs / "sgy.json"), ps_file=str(inputs / "ps.json"), ixl_dir=str(inputs / "ixl"),
              state_file=str(state_dir / "school-state.json"))


def _meta(state_dir):
    p = state_dir / "sync_meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _count(state_dir, sql="SELECT COUNT(*) FROM assignments"):
    from school_dashboard.assignments import schema
    conn = schema.connect(str(state_dir / "school.db"))
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_flag_off_returns_none_and_touches_nothing(env):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    _write(inputs)
    assert ingest.run_shadow(_args(state_dir, inputs), now=NOW) is None
    assert not (state_dir / "school.db").exists()
    assert not (state_dir / "sync_meta.json").exists()


@pytest.mark.parametrize("value", ["0", "false", "off", ""])
def test_falsy_flag_values_stay_off(env, monkeypatch, value):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", value)
    assert ingest.run_shadow(_args(state_dir, inputs), now=NOW) is None
    assert not (state_dir / "sync_meta.json").exists()


def test_happy_path_ingests_and_records_ok(env, monkeypatch):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    _write(inputs)
    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert report is not None and report.ok
    assert _count(state_dir) == 2
    assert _meta(state_dir)["recon_ingest"]["last_result"] == "ok"
    assert _count(state_dir, "SELECT COUNT(*) FROM recon_meta WHERE key = 'last_ingest_ok'") == 1


def test_file_hashes_are_sha256_of_the_bytes(env, monkeypatch):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    _write(inputs)
    seen = {}

    def spy(db_path, results, *, file_hashes, now, no_school_rows, roster=None):
        seen.update(file_hashes)
        return ingest.IngestReport(True, (), (), False, "x")

    monkeypatch.setattr(ingest, "run", spy)
    ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert seen["schoology"] == hashlib.sha256((inputs / "sgy.json").read_bytes()).hexdigest()
    assert seen["powerschool"] == hashlib.sha256((inputs / "ps.json").read_bytes()).hexdigest()


def test_stale_powerschool_file_is_not_trusted(env, monkeypatch):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    _write(inputs, ps_stamp="2026-11-10T10:00:00-05:00")
    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert report is not None and report.ok
    assert _count(state_dir) == 1  # only the Schoology row
    assert any("powerschool" in w for w in report.warnings)


def test_missing_files_contribute_nothing_with_a_warning(env, monkeypatch):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert report is not None and report.ok and report.kids == ()
    assert any("schoology" in w for w in report.warnings) and any("powerschool" in w for w in report.warnings)
    assert _count(state_dir) == 0


def test_calendar_not_covering_today_warns(env, monkeypatch):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    _write(inputs)
    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert report is not None and "school calendar does not cover today" in report.warnings
    from school_dashboard.assignments import schema
    conn = schema.connect(str(state_dir / "school.db"))
    conn.execute("INSERT INTO events (date, title, type, child) VALUES ('2026-12-01', 'Day off', 'NO_SCHOOL', NULL)")
    conn.close()
    again = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert again is not None and "school calendar does not cover today" not in again.warnings


def test_not_migrated_is_recorded_as_error_with_a_clear_message(env, monkeypatch, caplog):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    _write(inputs)
    caplog.set_level(logging.ERROR)
    assert ingest.run_shadow(_args(state_dir, inputs), now=NOW) is None
    assert _meta(state_dir)["recon_ingest"]["last_result"] == "error"
    assert "school-state assignments migrate" in caplog.text
    assert not (state_dir / "school.db").exists()


def test_failure_never_raises_never_prints_and_logs_type_name_only(env, monkeypatch, capsys, caplog):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    _write(inputs)

    def boom(*a, **k):
        raise RuntimeError("secret detail Worksheet")

    monkeypatch.setattr(ingest, "run", boom)
    caplog.set_level(logging.ERROR)
    assert ingest.run_shadow(_args(state_dir, inputs), now=NOW) is None
    assert capsys.readouterr().out == ""
    assert "RuntimeError" in caplog.text and "secret detail" not in caplog.text
    assert _meta(state_dir)["recon_ingest"]["last_result"] == "error"


def test_after_ingest_seam_runs_after_success_only(env, monkeypatch):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    _write(inputs)
    calls = []
    ingest.run_shadow(_args(state_dir, inputs), now=NOW, after_ingest=lambda conn, now: calls.append(now))
    assert calls == [NOW]

    def failing(conn, now):
        raise OSError("push exploded")

    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW, after_ingest=failing)
    assert report is not None  # a sender failure is not an ingest failure
    assert _meta(state_dir)["recon_ingest"]["last_result"] == "ok"
    monkeypatch.setattr(ingest, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    calls.clear()
    ingest.run_shadow(_args(state_dir, inputs), now=NOW, after_ingest=lambda conn, now: calls.append(now))
    assert calls == []


def _update_in(root, monkeypatch, flag, raising=False):
    """Run `school-state update` over the contract fixtures in its own state dir; (bytes, stdout, meta)."""
    state_dir = root / "state"
    state_dir.mkdir(parents=True)
    monkeypatch.setattr(scrape_meta, "_utc_now", lambda: datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(state_dir))
    set_roster(root, monkeypatch)
    if flag:
        monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
        make_db(state_dir)
    else:
        monkeypatch.delenv("RECON_INGEST_ENABLED", raising=False)
    if raising:
        from school_dashboard.assignments import ingest
        monkeypatch.setattr(ingest, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    built = build_state(state_dir, root)
    return built["path"].read_bytes(), _meta(state_dir), state_dir


def test_update_hook_failure_leaves_v1_output_identical(tmp_path, monkeypatch, capsys):
    off_bytes, off_meta, _ = _update_in(tmp_path / "off", monkeypatch, flag=False)
    off_out = capsys.readouterr().out
    on_bytes, on_meta, _ = _update_in(tmp_path / "on", monkeypatch, flag=True, raising=True)
    on_out = capsys.readouterr().out
    assert on_bytes == off_bytes
    assert on_out == off_out == ""
    assert off_meta == {}
    assert on_meta["recon_ingest"]["last_result"] == "error"


def test_update_hook_happy_path_records_ok_and_rows(tmp_path, monkeypatch):
    _, meta, state_dir = _update_in(tmp_path / "on", monkeypatch, flag=True)
    assert meta["recon_ingest"]["last_result"] == "ok"
    assert _count(state_dir) > 0


def test_update_flag_off_matches_state_with_and_without_recon_present(tmp_path, monkeypatch):
    first, meta, state_dir = _update_in(tmp_path / "a", monkeypatch, flag=False)
    assert meta == {} and not (state_dir / "school.db").exists()
    second, _, _ = _update_in(tmp_path / "b", monkeypatch, flag=False)
    assert first == second
