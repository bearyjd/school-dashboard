"""Ingest engine: refusal, per-kid isolation, locking, baseline, counts, hygiene."""
import re
import sqlite3
import threading
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from recon.ingest_support import WED, build_results, make_db, snapshot
from recon.support import mk_link

SGY = {"Alex": [{"title": "Worksheet 3", "course": "Math 6", "due": "2026-11-20", "link": "https://sgy.example.invalid/a/1"}]}
PS = {"Alex": {"upcoming": [{"course": "Math 6", "title": "Worksheet 3", "due": "2026-11-20"}]}}
HASH = {"schoology": "s1", "powerschool": "p1"}
PACKAGE = Path(__file__).resolve().parents[2] / "school_dashboard" / "assignments"


def _run(db_path, run, now=WED, rows=()):
    from school_dashboard.assignments import ingest
    results, hashes = build_results(run)
    return ingest.run(db_path, results, file_hashes=hashes, now=now, no_school_rows=list(rows))


def _kid(report, name):
    return next(k for k in report.kids if k.kid == name)


def test_not_migrated_is_refused_and_writes_nothing(tmp_path):
    from school_dashboard.assignments import repo
    db_path = make_db(tmp_path, migrated=False)
    with pytest.raises(repo.NotMigratedError, match="school-state assignments migrate"):
        _run(db_path, {"sgy": SGY, "hash": HASH})
    assert snapshot(db_path)["assignments"] == {}


def test_missing_db_file_is_refused_not_created(tmp_path):
    from school_dashboard.assignments import repo
    target = tmp_path / "nope.db"
    with pytest.raises(repo.NotMigratedError):
        _run(str(target), {"sgy": SGY, "hash": HASH})
    assert not target.exists()


def test_db_without_recon_tables_is_refused(tmp_path):
    from school_dashboard import db
    from school_dashboard.assignments import repo
    path = str(tmp_path / "legacy.db")
    db.init_db(path)
    with pytest.raises(repo.NotMigratedError):
        _run(path, {"sgy": SGY, "hash": HASH})


def test_kid_counts_and_frozen_report(tmp_path):
    db_path = make_db(tmp_path)
    first = _run(db_path, {"sgy": SGY, "ps": PS, "hash": HASH})
    alex = _kid(first, "Alex")
    assert (alex.ok, alex.created, alex.merged, alex.linked, alex.ambiguous, alex.absent) == (True, 1, 1, 0, 0, 0)
    assert alex.flag_changes == 1 and alex.pushes_enqueued == 0 and alex.error is None
    assert first.ok and first.baseline is False and first.data_as_of
    again = _run(db_path, {"sgy": SGY, "ps": PS, "hash": {"schoology": "s2", "powerschool": "p2"}})
    assert (_kid(again, "Alex").created, _kid(again, "Alex").linked) == (0, 2)
    with pytest.raises(FrozenInstanceError):
        setattr(first, "ok", False)
    with pytest.raises(FrozenInstanceError):
        setattr(alex, "created", 5)


def test_failure_rolls_back_only_that_kid(tmp_path, monkeypatch):
    from school_dashboard.assignments import flags
    real = flags.compute_flag

    def boom(state, links, **kw):
        if state.kid == "Sam":
            raise ValueError("synthetic failure")
        return real(state, links, **kw)

    monkeypatch.setattr(flags, "compute_flag", boom)
    db_path = make_db(tmp_path, {"seed": []}, baseline=False)
    report = _run(db_path, {"sgy": {**SGY, "Sam": SGY["Alex"]}, "hash": HASH})
    assert report.ok is False
    assert _kid(report, "Alex").ok is True
    sam = _kid(report, "Sam")
    assert sam.ok is False and sam.error == "ValueError"
    snap = snapshot(db_path)
    assert [k for k in snap["assignments"] if k.startswith("Sam|")] == []
    assert [k for k in snap["assignments"] if k.startswith("Alex|")]
    assert snap["baseline_at"] is None  # a failed kid keeps the next run a baseline run


def test_baseline_is_set_once_when_all_kids_succeed(tmp_path):
    db_path = make_db(tmp_path, baseline=False)
    report = _run(db_path, {"sgy": SGY, "hash": HASH})
    assert report.ok and report.baseline is True
    stamp = snapshot(db_path)["baseline_at"]
    assert stamp is not None
    later = _run(db_path, {"sgy": SGY, "hash": {"schoology": "s2"}})
    assert later.baseline is False and snapshot(db_path)["baseline_at"] == stamp


def test_nothing_processed_does_not_set_baseline(tmp_path):
    db_path = make_db(tmp_path, baseline=False)
    report = _run(db_path, {"sgy": SGY, "sgy_top": {"warnings": ["x"]}, "hash": HASH})
    assert report.ok and report.kids == ()
    assert snapshot(db_path)["baseline_at"] is None


def test_second_connection_holding_the_lock_makes_kids_wait_or_report_busy(tmp_path, monkeypatch):
    from school_dashboard.assignments import repo, schema
    monkeypatch.setattr(schema, "BUSY_TIMEOUT_MS", 60)
    db_path = make_db(tmp_path)
    blocker = schema.connect(db_path)
    blocker.execute("BEGIN IMMEDIATE")
    real = repo.transaction
    calls = []

    def releasing(conn):
        ctx = real(conn)
        try:
            ctx.__enter__()
        except repo.BusyError:
            calls.append("busy")
            if blocker.in_transaction:
                blocker.execute("ROLLBACK")  # the tap finishes after the first kid gave up
            raise
        calls.append("ok")
        return _Entered(ctx, conn)

    class _Entered:
        def __init__(self, ctx, conn):
            self.ctx, self.conn = ctx, conn

        def __enter__(self):
            return self.conn

        def __exit__(self, *exc):
            return self.ctx.__exit__(*exc)

    monkeypatch.setattr(repo, "transaction", releasing)
    report = _run(db_path, {"sgy": {**SGY, "Sam": SGY["Alex"]}, "hash": HASH})
    blocker.close()
    assert calls[0] == "busy" and "ok" in calls[1:]
    assert report.ok is False
    results = {k.kid: k for k in report.kids}
    assert sorted(k.ok for k in results.values()) == [False, True]
    assert [k.error for k in results.values() if not k.ok] == ["BusyError"]


def test_ingest_waits_for_a_short_lock(tmp_path):
    from school_dashboard.assignments import schema
    db_path = make_db(tmp_path)
    blocker = schema.connect(db_path)
    blocker.execute("BEGIN IMMEDIATE")

    def release():
        time.sleep(0.3)
        blocker.execute("COMMIT")

    t = threading.Thread(target=release)
    t.start()
    report = _run(db_path, {"sgy": SGY, "hash": HASH})
    t.join()
    blocker.close()
    assert report.ok and len(snapshot(db_path)["assignments"]) == 1


def test_inputs_are_not_mutated(tmp_path):
    from school_dashboard.assignments import ingest
    db_path = make_db(tmp_path)
    results, hashes = build_results({"sgy": SGY, "ps": PS, "hash": HASH})
    before_results, before_hashes = tuple(results), dict(hashes)
    rows = [{"date": "2026-11-23", "type": "NO_SCHOOL", "child": None}]
    ingest.run(db_path, results, file_hashes=hashes, now=WED, no_school_rows=rows)
    assert tuple(results) == before_results and hashes == before_hashes
    assert rows == [{"date": "2026-11-23", "type": "NO_SCHOOL", "child": None}]


def test_no_stdout_and_logs_carry_counts_only(tmp_path, capsys, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    db_path = make_db(tmp_path)
    _run(db_path, {"sgy": SGY, "ps": PS, "hash": HASH})
    out = capsys.readouterr()
    assert out.out == ""
    text = " ".join(r.getMessage() for r in caplog.records)
    for secret in ("Worksheet", "Math 6", "Alex", "sgy.example.invalid"):
        assert secret not in text


def test_no_sql_deletes_or_event_rewrites_in_ingest_modules():
    sources = [p for p in PACKAGE.glob("ingest*.py")] + [PACKAGE / "stages.py"]
    assert any(p.name == "ingest.py" for p in sources)
    for path in sources:
        text = path.read_text()
        assert not re.search(r"DELETE\s+FROM", text, re.I), path.name
        assert not re.search(r"UPDATE\s+assignment_events", text, re.I), path.name


def test_new_modules_stay_small():
    for path in PACKAGE.glob("ingest*.py"):
        assert len(path.read_text().splitlines()) < 400, path.name


def test_mark_absent_uncounted_sets_state_without_counting():
    from datetime import date
    from school_dashboard.assignments import stages
    link = mk_link(source="schoology", trusted_misses=0)
    today = date(2026, 11, 18)
    out = stages.mark_absent_uncounted(link, today)
    assert (out.live, out.absent_since, out.trusted_misses) == (False, today, 0)
    assert link.live is True  # input untouched
    again = stages.mark_absent_uncounted(out, date(2026, 11, 20))
    assert again.absent_since == today and again.trusted_misses == 0
    assert replace(link, live=False) is not link


def test_only_parameterised_sql_in_ingest_modules():
    for path in PACKAGE.glob("ingest*.py"):
        text = path.read_text()
        assert not re.search(r"execute\(\s*f[\"']", text), path.name
