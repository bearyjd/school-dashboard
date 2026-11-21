"""S3 review round (ingest side): roster filter, no false-healthy ingest, hook outside the state lock,
record/size caps, shadow logging, stage evidence sources, mass-change boundaries. SYNTHETIC data only."""
import json
import logging
import os
import sqlite3
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from recon.ingest_support import WED, build_results, make_db, ns, snapshot
from recon.report_support import sgy as seed_sgy
from recon.test_shadow_hook import _args, _count, _meta, _update_in, _write, env  # noqa: F401  (env is a fixture)

NOW = WED


def _run(db, run, **kw):
    from school_dashboard.assignments import ingest
    results, hashes = build_results(run)
    return ingest.run(db, results, file_hashes=hashes, now=kw.pop("now", NOW), no_school_rows=[], **kw)


def _row(title="Worksheet", **kw):
    return {"title": title, "course": "Math 6", "due": "2026-11-20", "link": f"https://sgy.example.invalid/{title}", **kw}


# ---- A: roster filter ----------------------------------------------------------

def test_off_roster_kid_creates_no_rows_flags_or_outbox(tmp_path):
    db = make_db(tmp_path, baseline=False)
    report = _run(db, {"sgy": {"Alex": [_row("A")], "Zed": [_row("Z1"), _row("Z2")]},
                       "ps": {"Zed": {"missing": [{"title": "Late", "course": "Sci", "due": "2026-11-10"}]}}},
                  roster=frozenset({"Alex"}))
    snap = snapshot(db)
    assert {k.split("|")[0] for k in snap["assignments"]} == {"Alex"}
    assert snap["outbox_total"] == 0
    assert [k.kid for k in report.kids] == ["Alex"]
    joined = " | ".join(report.warnings)
    assert "3 records for 1 names not on the roster were ignored" in joined
    assert "Zed" not in joined


def test_off_roster_name_gets_no_trust_so_no_absence_and_no_row(tmp_path):
    db = make_db(tmp_path, {"seed": [{**seed_sgy("Old", "Math 6", "https://sgy.example.invalid/Old"), "kid": "Zed"}]})
    report = _run(db, {"sgy": {"Zed": []}}, roster=frozenset({"Alex"}))
    assert report.kids == ()
    assert snapshot(db)["assignments"]["Zed|Old|Math 6"]["links"]["schoology"]["live"] is True


def test_roster_kid_is_unaffected_and_no_warning_when_nobody_is_dropped(tmp_path):
    db = make_db(tmp_path)
    report = _run(db, {"sgy": {"Alex": [_row("A")]}}, roster=frozenset({"Alex", "Sam"}))
    assert len(snapshot(db)["assignments"]) == 1
    assert not any("roster" in w for w in report.warnings)


def test_no_roster_keeps_every_kid(tmp_path):
    db = make_db(tmp_path)
    _run(db, {"sgy": {"Alex": [_row("A")], "Zed": [_row("Z")]}}, roster=None)
    assert len(snapshot(db)["assignments"]) == 2
    db2 = make_db(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path)
    _run(db2, {"sgy": {"Alex": [_row("A")], "Zed": [_row("Z")]}})  # default: no roster
    assert len(snapshot(db2)["assignments"]) == 2


def test_shadow_filters_by_the_configured_roster(env, monkeypatch):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    _write(inputs, ps=False)
    data = json.loads((inputs / "sgy.json").read_text())
    data["per_child"].append({"child": {"name": "Zed Quux"}, "scrape_confidence": "high", "assignments": [
        {"title": "Stray", "course": "Art", "due_date": "2026-11-20", "status": "unknown", "link": ""}]})
    (inputs / "sgy.json").write_text(json.dumps(data))
    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert report is not None and [k.kid for k in report.kids] == ["Alpha"]
    assert _count(state_dir) == 1
    assert any("not on the roster" in w for w in report.warnings)


def test_shadow_with_an_empty_roster_keeps_todays_behaviour(env, monkeypatch, tmp_path):
    from school_dashboard import state
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    cfg = tmp_path / "empty-roster.json"
    cfg.write_text(json.dumps({"children": {}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.setattr(state, "_config_cache", None)
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    _write(inputs, ps=False)
    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert report is not None and _count(state_dir) == 1


# ---- B: nothing ingested must not look healthy ------------------------------------

def _meta_value(state_dir, key):
    return _count(state_dir, f"SELECT COUNT(*) FROM recon_meta WHERE key = '{key}'")


def test_all_files_missing_records_nothing_and_sets_no_baseline(env, monkeypatch, caplog):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir, baseline=False)
    caplog.set_level(logging.INFO)
    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert report is not None and report.kids == ()
    assert "recon_ingest" not in _meta(state_dir)
    assert _meta_value(state_dir, "last_ingest_ok") == 0 and _meta_value(state_dir, "baseline_at") == 0
    assert sum("nothing ingested" in r.getMessage() for r in caplog.records if r.levelno == logging.INFO) == 1


def test_all_untrusted_records_nothing_and_leaves_existing_ok_alone(env, monkeypatch):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir, baseline=False)
    _write(inputs, ps=False)
    data = json.loads((inputs / "sgy.json").read_text())
    data["warnings"] = ["scraper said so"]  # top-level warnings make every entry untrusted
    (inputs / "sgy.json").write_text(json.dumps(data))
    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert report is not None and report.kids == ()
    assert _meta(state_dir) == {}
    assert _meta_value(state_dir, "last_ingest_ok") == 0 and _meta_value(state_dir, "baseline_at") == 0


def test_nothing_ingested_still_lets_the_sender_flush_earlier_pushes(env, monkeypatch):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    calls = []
    ingest.run_shadow(_args(state_dir, inputs), now=NOW, after_ingest=lambda c, n: calls.append(n))
    assert calls == [NOW]  # queued pushes are not a health claim; only recon_ingest/baseline stay untouched


# ---- C: hook outside the state lock ------------------------------------------------

def test_hook_runs_after_the_state_lock_is_released(tmp_path, monkeypatch):
    from school_dashboard import statelock
    from school_dashboard.assignments import ingest
    held = []

    def probe(*a, **k):
        try:
            with statelock.state_lock(Path(os.environ["SCHOOL_STATE_DIR"]) / "school-state.json", timeout=0.3):
                held.append(False)
        except statelock.StateLockTimeout:
            held.append(True)

    monkeypatch.setattr(ingest, "run_shadow", probe)
    _update_in(tmp_path / "on", monkeypatch, flag=True)
    assert held == [False]


def test_update_locked_no_longer_knows_about_recon():
    import inspect
    from school_dashboard import cli
    assert "recon" not in inspect.getsource(cli._update_locked)
    assert "_recon_shadow" in inspect.getsource(cli.cmd_update)


def test_default_send_uses_a_connect_read_timeout_tuple():
    from unittest.mock import MagicMock
    from school_dashboard.assignments import notify
    with patch.object(notify.requests, "post", return_value=MagicMock()) as post:
        notify.default_send("topic", "body", "title")
    assert post.call_args.kwargs["timeout"] == (5, 10)


# ---- F: caps -------------------------------------------------------------------------

def test_kid_over_the_record_cap_is_inert_for_that_source(tmp_path, monkeypatch):
    from school_dashboard.assignments import ingest
    monkeypatch.setattr(ingest, "MAX_RECORDS_PER_KID", 3)
    db = make_db(tmp_path)
    report = _run(db, {"sgy": {"Alex": [_row(f"A{i}") for i in range(4)], "Sam": [_row("S1")]}})
    snap = snapshot(db)
    assert {k.split("|")[0] for k in snap["assignments"]} == {"Sam"}
    assert any("record cap" in w for w in report.warnings)
    assert [k.kid for k in report.kids] == ["Sam"]


def test_kid_exactly_at_the_record_cap_is_ingested(tmp_path, monkeypatch):
    from school_dashboard.assignments import ingest
    monkeypatch.setattr(ingest, "MAX_RECORDS_PER_KID", 3)
    db = make_db(tmp_path)
    _run(db, {"sgy": {"Alex": [_row(f"A{i}") for i in range(3)]}})
    assert len(snapshot(db)["assignments"]) == 3


def test_oversized_raw_json_is_stored_as_a_truncation_marker(tmp_path):
    from school_dashboard.assignments import ingest, schema
    from school_dashboard.assignments.adapters import ADAPTERS
    from recon.adapter_support import resolve
    db = make_db(tmp_path)
    payload = {"per_child": [{"child": {"name": "Alex"}, "scrape_confidence": "high", "assignments": [
        {"title": "Big", "course": "M", "due_date": "2026-11-20", "status": "unknown", "link": "", "blob": "x" * 20000},
        {"title": "Small", "course": "M", "due_date": "2026-11-20", "status": "unknown", "link": ""}]}]}
    res = ADAPTERS["schoology"].parse(payload, resolve_kid=resolve)
    ingest.run(db, [res], file_hashes={"schoology": "h"}, now=NOW, no_school_rows=[])
    conn = schema.connect(db)
    raws = {r["external_key"]: r["raw_json"] for r in conn.execute("SELECT external_key, raw_json FROM source_links")}
    conn.close()
    big = next(v for v in raws.values() if "truncated" in v)
    assert json.loads(big)["truncated"] is True and json.loads(big)["bytes"] > 8192 and len(big) < 200
    assert sum("truncated" in v for v in raws.values()) == 1


def test_scrape_file_over_the_size_cap_contributes_nothing(tmp_path, monkeypatch):
    from school_dashboard.assignments import ingest_io
    monkeypatch.setattr(ingest_io, "MAX_SCRAPE_BYTES", 100)
    big = tmp_path / "sgy.json"
    big.write_text(json.dumps({"per_child": [], "pad": "x" * 200}))
    result, digest, warning = ingest_io._load_sgy(str(big), lambda n: n, NOW)
    assert result is None and digest is None and warning and "too large" in warning


def test_scrape_file_exactly_at_the_cap_is_read(tmp_path, monkeypatch):
    from school_dashboard.assignments import ingest_io
    body = json.dumps({"per_child": []})
    monkeypatch.setattr(ingest_io, "MAX_SCRAPE_BYTES", len(body))
    f = tmp_path / "sgy.json"
    f.write_text(body)
    result, _, warning = ingest_io._load_sgy(str(f), lambda n: n, NOW)
    assert result is not None and warning is None


def test_cap_is_enforced_by_a_bounded_read_not_a_stat(tmp_path, monkeypatch):
    from school_dashboard.assignments import ingest_io
    monkeypatch.setattr(ingest_io, "MAX_SCRAPE_BYTES", 10)
    f = tmp_path / "sgy.json"
    f.write_text("[" + " " * 50 + "]")
    monkeypatch.setattr(Path, "stat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("stat used")))
    problem = ingest_io._read_json(f)[2]
    assert problem is not None and "too large" in problem


def test_deeply_nested_scrape_json_is_unreadable_not_a_crash(tmp_path):
    from school_dashboard.assignments import ingest_io
    f = tmp_path / "sgy.json"
    f.write_text("[" * 200000)
    result, digest, warning = ingest_io._load_sgy(str(f), lambda n: n, NOW)
    assert result is None and digest is None and warning


def test_max_scrape_bytes_constant_is_20_mib():
    from school_dashboard.assignments import ingest, ingest_io
    assert ingest_io.MAX_SCRAPE_BYTES == 20 * 1024 * 1024 and ingest.MAX_RECORDS_PER_KID == 2000


# ---- G1: shadow logging (counts only) ---------------------------------------------------

def test_run_shadow_logs_warning_count_and_mass_change_count_only(env, monkeypatch, caplog):
    from school_dashboard.assignments import ingest
    state_dir, inputs = env
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    make_db(state_dir)
    warns = ("schoology: mass change guard held back absence for 4 links Worksheet",
             "powerschool: mass change guard held back absence for 3 links", "other thing")
    monkeypatch.setattr(ingest, "run", lambda *a, **k: ingest.IngestReport(
        True, (ingest.KidResult(kid="Alpha", ok=True),), warns, False, "x"))
    caplog.set_level(logging.INFO)
    report = ingest.run_shadow(_args(state_dir, inputs), now=NOW)
    assert report is not None
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    guards = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert f"recon ingest: {len(report.warnings)} warnings" in infos
    assert any("2" in m and "mass change" in m for m in guards)
    assert "Worksheet" not in caplog.text and "4 links" not in caplog.text


# ---- G7: stage event evidence sources ------------------------------------------------------

def test_stage_event_to_4_names_the_evidence_sources_not_titles(tmp_path):
    from school_dashboard.assignments import schema
    db = make_db(tmp_path)
    _run(db, {"sgy": {"Alex": [_row("Essay", status="graded")]},
              "ps": {"Alex": {"late": [{"title": "Essay", "course": "Math 6", "due": "2026-11-20", "score": "9/10"}]}}})
    conn = schema.connect(db)
    rows = conn.execute("SELECT detail FROM assignment_events WHERE kind = 'stage' AND to_value = '4'").fetchall()
    conn.close()
    assert [r["detail"] for r in rows] == ["powerschool,schoology"]


# ---- H: mass-change boundaries --------------------------------------------------------------

@pytest.mark.parametrize("unseen, live, held", [
    (3, 5, True), (3, 6, False), (4, 6, True), (2, 4, False), (2, 2, True), (1, 1, False), (0, 0, False),
    (3, 3, True), (4, 8, False), (5, 9, True)])
def test_mass_change_boundaries(unseen, live, held):
    from school_dashboard.assignments.ingest_status import _mass_change
    assert _mass_change(unseen, live) is held


def _absence_run(tmp_path, live, unseen):
    keys = [f"https://sgy.example.invalid/{i}" for i in range(live)]
    seed = [{**seed_sgy(f"T{i}", "Math 6", k, due="2026-11-30")} for i, k in enumerate(keys)]
    db = make_db(tmp_path, {"seed": seed})
    seen = [_row(f"T{i}", due="2026-11-30") | {"link": keys[i]} for i in range(live - unseen)]
    seen = [{"title": s["title"], "course": "Math 6", "due": "2026-11-30", "link": s["link"]} for s in seen]
    return _run(db, {"sgy": {"Alex": seen}}), db


@pytest.mark.parametrize("live, unseen, expect_warning", [(5, 3, True), (6, 3, False)])
def test_mass_change_guard_end_to_end(tmp_path, live, unseen, expect_warning):
    report, db = _absence_run(tmp_path, live, unseen)
    assert any("mass change guard" in w for w in report.warnings) is expect_warning
