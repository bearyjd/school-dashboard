"""school-state calendar import: validation, mapping, replace semantics, CLI."""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from school_dashboard import db, school_calendar
from school_dashboard.digest import _query_db_events

REPO = Path(__file__).resolve().parent.parent
REAL_FILE = REPO / "config" / "calendars" / "st-mark-2026-27.json"


def _cal(events=None, **over):
    base = {
        "calendar_id": "test-cal",
        "school": "S",
        "school_year": "2026-27",
        "source": "x",
        "roster_grades": {"Ford": "3rd", "Penn": "6th", "Jack": "8th"},
        "events": events if events is not None else [],
    }
    base.update(over)
    return base


def _ev(date="2026-09-01", title="T", kind="event", rel=("Ford", "Penn", "Jack")):
    return {"date": date, "title": title, "kind": kind, "relevant_to": list(rel)}


def _write(tmp_path, data, name="cal.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data))
    return p


def _rows(db_file):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(
            "SELECT date,title,type,child,source FROM events ORDER BY id").fetchall()
    finally:
        conn.close()


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# --- validation ---

LONG = "x" * 201

@pytest.mark.parametrize("bad", [
    _cal(events=[_ev(rel=("Fordd",))]),
    _cal(events=[]),
    _cal(events=[_ev(title=LONG)]),
    _cal(events=[_ev(title="a\x00b")]),
    _cal(events=[_ev(title="a\nb")]),
    _cal(roster_grades={"x" * 51: "3rd"}, events=[_ev(rel=("x" * 51,))]),
    _cal(roster_grades={"Fo\x07rd": "3rd"}, events=[_ev(rel=("Fo\x07rd",))]),
    _cal(events=[_ev(title="Mass "), _ev(title="Mass")]),
])
def test_hardened_validation(state_env, tmp_path, bad):
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.validate(bad)
    f = _write(tmp_path, bad)
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.import_calendar(f, state_env / "school.db", dry_run=False)
    assert not (state_env / "school.db").exists()


def test_error_message_truncates_repr():
    with pytest.raises(school_calendar.CalendarError) as ei:
        school_calendar.validate(_cal(events=[_ev(kind="k" * 5000)]))
    assert len(str(ei.value)) < 600


def test_title_stripped_on_store(state_env, tmp_path):
    db_file = state_env / "school.db"
    school_calendar.import_calendar(
        _write(tmp_path, _cal(events=[_ev(title="  Mass ")])), db_file, dry_run=False)
    assert _rows(db_file)[0][1] == "Mass"


def test_oversize_file_rejected(tmp_path):
    p = tmp_path / "big.json"
    p.write_text(json.dumps(_cal(events=[_ev()])) + " " * (1024 * 1024 + 1))
    with pytest.raises(school_calendar.CalendarError, match="too large"):
        school_calendar.load_file(p)


def test_non_regular_file_rejected(tmp_path):
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.load_file(tmp_path)
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.load_file("/dev/null")


def test_deeply_nested_json_clean_error(tmp_path):
    p = tmp_path / "deep.json"
    p.write_text("[" * 200000)
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.load_file(p)
    r = _run(tmp_path, "calendar", "import", str(p), "--dry-run")
    assert r.returncode == 2 and "Traceback" not in r.stderr


def test_cli_legacy_events_table_exit_1(state_env, tmp_path):
    conn = sqlite3.connect(state_env / "school.db")
    conn.execute("CREATE TABLE events(date,title,type,child)")
    conn.commit()
    conn.close()
    f = _write(tmp_path, _cal(events=[_ev()]))
    r = _run(state_env, "calendar", "import", str(f))
    assert r.returncode == 1 and "Traceback" not in r.stderr and "Error" in r.stderr
    r = _run(state_env, "calendar", "import", str(f), "--dry-run")
    assert r.returncode == 0, r.stderr


def test_cli_corrupt_db(state_env, tmp_path):
    (state_env / "school.db").write_bytes(b"garbage" * 1000)
    f = _write(tmp_path, _cal(events=[_ev()]))
    r = _run(state_env, "calendar", "import", str(f))
    assert r.returncode == 1 and "Traceback" not in r.stderr
    r = _run(state_env, "calendar", "import", str(f), "--dry-run")
    assert r.returncode == 0 and "Traceback" not in r.stderr

@pytest.mark.parametrize("bad", [
    _cal(calendar_id="Bad_ID"),
    _cal(events=[_ev(date="2026-02-30")]),
    _cal(events=[_ev(date="09/01/2026")]),
    _cal(events=[_ev(title="  ")]),
    _cal(events=[_ev(kind="party")]),
    _cal(events=[{"date": "2026-09-01", "title": "T", "kind": "event", "relevant_to": "Ford"}]),
    _cal(events=[_ev(rel=("Ford", ""))]),
    _cal(events=[_ev(), _ev()]),
    {"calendar_id": "ok", "events": []},
])
def test_invalid_files_fail_closed(state_env, tmp_path, bad):
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.validate(bad)
    db_file = state_env / "school.db"
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.import_calendar(_write(tmp_path, bad), db_file, dry_run=False)
    assert not db_file.exists()


def test_non_object_and_bad_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("[1]")
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.load_file(p)
    p.write_text("{nope")
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.load_file(p)
    with pytest.raises(school_calendar.CalendarError):
        school_calendar.load_file(tmp_path / "missing.json")


def test_same_title_different_dates_ok():
    school_calendar.validate(_cal(events=[_ev(date="2026-09-01"), _ev(date="2026-09-02")]))


# --- mapping ---

def test_mapping(state_env, tmp_path):
    data = _cal(events=[
        _ev(title="All", kind="no_school"),
        _ev(title="Two", kind="half_day", rel=("Ford", "Jack")),
        _ev(title="None", kind="mass", rel=()),
    ])
    db_file = state_env / "school.db"
    rep = school_calendar.import_calendar(_write(tmp_path, data), db_file, dry_run=False)
    rows = _rows(db_file)
    assert rows == [
        ("2026-09-01", "All", "NO_SCHOOL", None, "calendar:test-cal"),
        ("2026-09-01", "Two", "HALF_DAY", "Ford", "calendar:test-cal"),
        ("2026-09-01", "Two", "HALF_DAY", "Jack", "calendar:test-cal"),
    ]
    assert rep["inserted"] == 3 and rep["skipped"] == 1 and rep["deleted"] == 0
    assert rep["by_type"] == {"NO_SCHOOL": 1, "HALF_DAY": 2}
    assert rep["date_range"] == ["2026-09-01", "2026-09-01"]


def test_idempotent_and_other_sources_untouched(state_env, tmp_path):
    db_file = state_env / "school.db"
    db.init_events_table(str(db_file))
    conn = sqlite3.connect(db_file)
    conn.execute("INSERT INTO events (date,title,type,child,source) VALUES "
                 "('2026-09-01','Manual','EVENT',NULL,'manual')")
    conn.execute("INSERT INTO events (date,title,type,child,source) VALUES "
                 "('2026-09-01','Other','EVENT',NULL,'calendar:other')")
    conn.commit()
    conn.close()
    f = _write(tmp_path, _cal(events=[_ev(title="A"), _ev(title="B", rel=("Ford",))]))
    school_calendar.import_calendar(f, db_file, dry_run=False)
    first = [r[:5] for r in _rows(db_file)]
    rep = school_calendar.import_calendar(f, db_file, dry_run=False)
    second = [r[:5] for r in _rows(db_file)]
    assert sorted(first) == sorted(second)
    assert len(second) == 4
    assert rep["deleted"] == 2 and rep["inserted"] == 2
    titles = {r[1] for r in second}
    assert {"Manual", "Other"} <= titles


def test_failed_insert_rolls_back(state_env, tmp_path, monkeypatch):
    db_file = state_env / "school.db"
    f = _write(tmp_path, _cal(events=[_ev(title="A")]))
    school_calendar.import_calendar(f, db_file, dry_run=False)
    before = _rows(db_file)
    f2 = _write(tmp_path, _cal(events=[_ev(title="B")]), "b.json")
    monkeypatch.setattr(school_calendar, "_insert_rows",
                        lambda conn, rows: (_ for _ in ()).throw(sqlite3.OperationalError("boom")))
    with pytest.raises(sqlite3.OperationalError):
        school_calendar.import_calendar(f2, db_file, dry_run=False)
    assert _rows(db_file) == before


# --- dry-run ---

def test_dry_run_leaves_db_identical(state_env, tmp_path):
    db_file = state_env / "school.db"
    f = _write(tmp_path, _cal(events=[_ev(title="A")]))
    school_calendar.import_calendar(f, db_file, dry_run=False)
    h = _sha(db_file)
    f2 = _write(tmp_path, _cal(events=[_ev(title="A"), _ev(title="B")]), "b.json")
    rep = school_calendar.import_calendar(f2, db_file, dry_run=True)
    assert _sha(db_file) == h
    assert rep["dry_run"] is True and rep["inserted"] == 2 and rep["deleted"] == 1


def test_dry_run_without_db_creates_nothing(state_env, tmp_path):
    db_file = state_env / "school.db"
    rep = school_calendar.import_calendar(
        _write(tmp_path, _cal(events=[_ev()])), db_file, dry_run=True)
    assert not db_file.exists()
    assert rep["deleted"] == 0 and rep["inserted"] == 1


def test_dry_run_db_without_events_table(state_env, tmp_path):
    db_file = state_env / "school.db"
    sqlite3.connect(db_file).close()
    h = _sha(db_file)
    rep = school_calendar.import_calendar(
        _write(tmp_path, _cal(events=[_ev()])), db_file, dry_run=True)
    assert rep["deleted"] == 0
    assert _sha(db_file) == h


# --- CLI end to end ---

def _run(state_dir, *args):
    import os
    env = {**os.environ, "SCHOOL_STATE_DIR": str(state_dir)}
    for k in ("SCHOOL_DB_PATH", "SCHOOL_STATE_PATH"):
        env.pop(k, None)
    return subprocess.run(
        [sys.executable, "-m", "school_dashboard.cli", *args],
        cwd=REPO, env=env, capture_output=True, text=True)


def test_cli_import_and_json(state_env, tmp_path):
    f = _write(tmp_path, _cal(events=[_ev(), _ev(title="Z", rel=())]))
    r = _run(state_env, "calendar", "import", str(f), "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["inserted"] == 1 and out["skipped"] == 1 and out["dry_run"] is False
    assert len(_rows(state_env / "school.db")) == 1
    r = _run(state_env, "calendar", "import", str(f))
    assert r.returncode == 0 and "inserted" in r.stdout.lower()


def test_cli_invalid_exit_2_no_writes(state_env, tmp_path):
    f = _write(tmp_path, _cal(events=[_ev(kind="bogus")]))
    r = _run(state_env, "calendar", "import", str(f))
    assert r.returncode == 2
    assert "kind" in r.stderr
    assert not (state_env / "school.db").exists()


def test_cli_maintenance_blocks_import_not_dry_run(state_env, tmp_path):
    f = _write(tmp_path, _cal(events=[_ev()]))
    assert _run(state_env, "transition", "maintenance", "on").returncode == 0
    r = _run(state_env, "calendar", "import", str(f))
    assert r.returncode == 3
    assert not (state_env / "school.db").exists()
    r = _run(state_env, "calendar", "import", str(f), "--dry-run", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["dry_run"] is True
    assert not (state_env / "school.db").exists()


def test_cli_missing_subcommand_exit_2(state_env):
    assert _run(state_env, "calendar").returncode == 2


# --- real data file ---

@pytest.mark.skipif(not REAL_FILE.exists(), reason="config/calendars/st-mark-2026-27.json not present")
def test_real_calendar_file(state_env):
    data = json.loads(REAL_FILE.read_text())
    assert len(data["events"]) > 0
    n = len(data["roster_grades"])
    by_len = {}
    for e in data["events"]:
        by_len[len(e["relevant_to"])] = by_len.get(len(e["relevant_to"]), 0) + 1
    # empty -> skipped; full roster -> 1 row; any other size k -> k rows
    exp_skip = by_len.get(0, 0)
    exp_rows = sum(c * (1 if k == n else k) for k, c in by_len.items() if k)
    db_file = state_env / "school.db"
    rep = school_calendar.import_calendar(REAL_FILE, db_file, dry_run=False)
    assert rep["skipped"] == exp_skip and rep["inserted"] == exp_rows
    assert len(_rows(db_file)) == exp_rows


# --- digest dedupe ---

def test_digest_collapses_identical_title_type(tmp_path):
    p = tmp_path / "s.db"
    db.init_events_table(str(p))
    conn = sqlite3.connect(p)
    for child in ("Ford", "Jack"):
        conn.execute("INSERT INTO events (date,title,type,child) VALUES "
                     "('2026-09-01','Field Trip','EVENT',?)", (child,))
    conn.execute("INSERT INTO events (date,title,type,child) VALUES "
                 "('2026-09-01','Mass','MASS',NULL)")
    conn.execute("INSERT INTO events (date,title,type,child) VALUES "
                 "('2026-09-01','Field Trip','EVENT',NULL)")
    conn.commit()
    conn.close()
    out = _query_db_events(str(p), "2026-09-01")
    assert [(e["title"], e["type"]) for e in out] == [("Field Trip", "EVENT"), ("Mass", "MASS")]
    # a NULL-child row means "everyone": the merged child stays empty
    assert not out[0]["child"]


def test_digest_joins_children_when_all_named(tmp_path):
    p = tmp_path / "s.db"
    db.init_events_table(str(p))
    conn = sqlite3.connect(p)
    for child in ("Ford", "Jack", "Ford"):
        conn.execute("INSERT INTO events (date,title,type,child) VALUES "
                     "('2026-09-01','Field Trip','EVENT',?)", (child,))
    conn.commit()
    conn.close()
    out = _query_db_events(str(p), "2026-09-01")
    assert len(out) == 1 and out[0]["child"] == "Ford, Jack"
