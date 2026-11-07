import sqlite3
from datetime import date

from school_dashboard.overview_events import gc_events, school_events, split_kids

ROSTER = ["Penn", "Jack"]
TODAY = date(2026, 11, 6)


def _team(label, events, **extra):
    return {"team_id": "t1", "team_name": "Lions", "child": label, "schedule": events, **extra}


def _evt(d, time="10:00", **extra):
    return {"date": d, "time": time, "type": "game", "opponent": "Hawks",
            "location": "Field 1", **extra}


def test_split_kids():
    assert split_kids("Penn+Jack", ROSTER) == ["Penn", "Jack"]
    assert split_kids("jack", ROSTER) == ["Jack"]
    assert split_kids("St. Mark Lions", ROSTER) == []
    assert split_kids("", ROSTER) == []


def test_gc_events_window_and_order():
    gc = {"teams": [_team("Penn", [
        _evt("2026-11-05"),            # past
        _evt("2026-11-10", "15:00"),
        _evt("2026-11-10", "09:00"),
        _evt("2026-11-06", "18:00"),
        _evt("2026-11-20"),            # outside 14 days (exclusive end)
        _evt("2026-11-19"),
    ])]}
    out = gc_events(gc, ROSTER, TODAY, 14)
    assert [(e["date"], e["time"]) for e in out] == [
        ("2026-11-06", "18:00"), ("2026-11-10", "09:00"),
        ("2026-11-10", "15:00"), ("2026-11-19", "10:00")]
    assert out[0]["kids"] == ["Penn"]
    assert out[0]["source"] == "gc"
    assert out[0]["team"] == "Lions"


def test_past_season_teams_hidden():
    gc = {"teams": [_team("Penn", [_evt("2026-01-01")])]}
    assert gc_events(gc, ROSTER, TODAY, 14) == []
    shown = gc_events(gc, ROSTER, TODAY, 14, include_past_seasons=True)
    assert [e["date"] for e in shown] == ["2026-01-01"]


def test_schedule_source_passthrough():
    gc = {"teams": [_team("Penn", [_evt("2026-11-07")], schedule_source="ics"),
                    _team("Jack", [_evt("2026-11-08")])]}
    out = gc_events(gc, ROSTER, TODAY, 14)
    assert {e["kids"][0]: e["scheduleSource"] for e in out} == {"Penn": "ics", "Jack": "api"}


def test_school_events_from_db(tmp_path):
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, date TEXT, title TEXT, type TEXT,"
                 " child TEXT, source TEXT)")
    conn.execute("INSERT INTO events (date,title,type,child,source) VALUES"
                 " ('2026-11-12','No School','HOLIDAY',NULL,'calendar:x')")
    conn.execute("INSERT INTO events (date,title,type,child,source) VALUES"
                 " ('2026-01-12','Old','TEST',NULL,'calendar:x')")
    conn.commit()
    conn.close()
    out = school_events(str(db), TODAY, 14)
    assert len(out) == 1
    assert out[0]["kids"] == [] and out[0]["title"] == "No School"
    assert out[0]["source"] == "school" and out[0]["scheduleSource"] is None
    assert school_events(str(tmp_path / "missing.db"), TODAY, 14) == []


def test_school_events_missing_table(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    assert school_events(str(db), TODAY, 14) == []


def test_bad_event_rows_skipped():
    gc = {"teams": [
        "junk",
        _team("Penn", [
            "x", None, {"date": "nope"}, {"time": "1"}, {"date": 5},
            _evt("2026-11-07", opponent="Z" * 5000),
            _evt("2026-11-07T10:00:00"),
        ]),
        {"team_name": "NoSchedule", "schedule": "bad"},
    ]}
    out = gc_events(gc, ROSTER, TODAY, 14)
    assert len(out) == 2
    assert all(len(e["title"]) <= 160 for e in out)
    assert gc_events({}, ROSTER, TODAY, 14) == []
    assert gc_events({"teams": "bad"}, ROSTER, TODAY, 14) == []


def test_unassigned_team_distinct_from_school_wide(tmp_path):
    gc = {"teams": [_team("St. Mark Lions", [_evt("2026-11-07")]),
                    _team("Penn", [_evt("2026-11-08")])]}
    out = gc_events(gc, ROSTER, TODAY, 14)
    assert [(e["kids"], e["unassigned"]) for e in out] == [([], True), (["Penn"], False)]
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, date TEXT, title TEXT, type TEXT,"
                 " child TEXT, source TEXT)")
    conn.execute("INSERT INTO events (date,title,type,child,source) VALUES"
                 " ('2026-11-12','No School','HOLIDAY',NULL,'x')")
    conn.commit()
    conn.close()
    assert school_events(str(db), TODAY, 14)[0]["unassigned"] is False


def test_events_sorted_by_parsed_time():
    gc = {"teams": [_team("Penn", [_evt("2026-11-07", "10:00 AM"), _evt("2026-11-07", "9:00 AM"),
                                   _evt("2026-11-07", "1:30 PM"), _evt("2026-11-07", "")])]}
    out = gc_events(gc, ROSTER, TODAY, 14)
    assert [e["time"] for e in out] == ["09:00", "10:00", "13:30", ""]
