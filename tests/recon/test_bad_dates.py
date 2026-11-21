"""One malformed calendar row or stored date degrades with a counted warning instead of failing v2 (SYNTHETIC data)."""
import argparse
import logging
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from recon.api_support import data, one, sgy
from recon.conftest import NOW
from recon.ingest_support import make_db
from recon.support import local

BAD = ["2025-09-31", "2026-02-30", "garbage", "", None, 20260930, b"2026-09-30"]
CAL = "1 calendar rows had an unreadable date; ignored"


def _sam(title="Poster", key="s9"):
    return {**sgy(title, "Art", key), "kid": "Sam"}


def _rows(*dates, type_="NO_SCHOOL", child=None):
    return [{"date": d, "type": type_, "child": child} for d in dates]


# ---- schooldays -------------------------------------------------------------

@pytest.mark.parametrize("bad", BAD)
def test_unreadable_calendar_dates_are_skipped_and_counted(bad):
    from school_dashboard.assignments import schooldays as sd
    rows = _rows(bad, "2026-11-20")
    assert sd.no_school_dates(rows, "Alex") == frozenset({date(2026, 11, 20)})
    assert sd.calendar_covers(rows, date(2026, 11, 18)) is True
    assert sd.calendar_covers(_rows(bad), date(2026, 11, 18)) is False
    assert sd.bad_calendar_rows(rows) == 1


def test_calendar_rows_tolerate_missing_keys_extra_columns_and_datetime_strings():
    from school_dashboard.assignments import schooldays as sd
    rows = [{"date": "2026-11-20T09:00:00", "type": "NO_SCHOOL", "notes": "x", "id": 4}, {"type": "NO_SCHOOL"}, {},
            {"date": "2026-11-21", "type": "SOMETHING_NEW", "child": "Alex,Sam"}]
    assert sd.no_school_dates(rows, "Alex") == frozenset({date(2026, 11, 20)})
    assert sd.bad_calendar_rows(rows) == 2
    assert sd.bad_calendar_rows([]) == 0


def test_calendar_warning_text_and_single_log(caplog):
    from school_dashboard.assignments import schooldays as sd
    sd.reset_calendar_log()
    caplog.set_level(logging.WARNING)
    assert sd.calendar_warning(0) is None and sd.calendar_warning(1) == CAL
    for _ in range(3):
        sd.log_bad_calendar(1)
    sd.log_bad_calendar(0)
    hits = [r for r in caplog.records if "unreadable date" in r.getMessage()]
    assert len(hits) == 1 and "2025" not in hits[0].getMessage()
    sd.reset_calendar_log()


# ---- shared parsers -----------------------------------------------------------

def test_parse_date_is_total_and_never_guesses():
    from school_dashboard.assignments.dates import parse_date
    assert parse_date("2026-11-20") == date(2026, 11, 20)
    assert parse_date("2026-11-20T10:00:00+00:00") == date(2026, 11, 20)
    assert parse_date(datetime(2026, 11, 20, 23, 0)) == date(2026, 11, 20)
    for bad in (*BAD, "2026-W47-5", "11/20/2026", "20261120", "2026-13-01", "2026-1-5"):
        assert parse_date(bad) is None


def test_parse_dt_is_total_and_zones_naive_values():
    from school_dashboard.assignments.dates import parse_dt
    tz = ZoneInfo("America/New_York")
    naive = parse_dt("2026-11-16T12:00:00", tz)
    assert naive is not None and naive.utcoffset() is not None and naive.hour == 12
    aware = parse_dt("2026-11-16T12:00:00+00:00", tz)
    assert aware is not None and aware.utcoffset() == timedelta(0)
    for bad in (None, "", "garbage", "2025-09-31T10:00:00", 5, b"x"):
        assert parse_dt(bad, tz) is None


# ---- stored assignment rows ----------------------------------------------------

def _corrupt(conn, aid, **cols):
    sets = ", ".join(f"{c} = ?" for c in cols)
    conn.execute(f"UPDATE assignments SET {sets} WHERE id = ?", (*cols.values(), aid))


def _conn(tmp_path, seed):
    from school_dashboard.assignments import schema
    return schema.connect(make_db(tmp_path, {"seed": seed}))


def test_unreadable_due_date_is_no_due_date_and_flagged(tmp_path):
    from school_dashboard.assignments import repo
    conn = _conn(tmp_path, one())
    _corrupt(conn, 1, due_date="2025-09-31")
    st = repo.get_assignment(conn, 1)
    assert st is not None and st.due_date is None and st.data_problem is True


def test_unreadable_stage_since_falls_back_to_created_at_and_flags(tmp_path):
    from school_dashboard.assignments import repo
    conn = _conn(tmp_path, one())
    _corrupt(conn, 1, stage_since="garbage")
    stamp = conn.execute("SELECT created_at FROM assignments WHERE id = 1").fetchone()[0]
    st = repo.get_assignment(conn, 1)
    assert st is not None and st.data_problem and st.stage_since == datetime.fromisoformat(stamp)
    _corrupt(conn, 1, stage_since="garbage", created_at="x", updated_at="y")
    st = repo.get_assignment(conn, 1)
    assert st is not None and st.data_problem and st.stage_since.utcoffset() is not None


def test_unreadable_optional_timestamps_are_none(tmp_path):
    from school_dashboard.assignments import repo
    conn = _conn(tmp_path, one())
    _corrupt(conn, 1, closed_at="nope")
    st = repo.get_assignment(conn, 1)
    assert st is not None and st.closed_at is None and st.data_problem


def test_naive_stored_timestamps_are_read_in_the_configured_zone(api_factory):
    api = api_factory(one())
    api.exec("UPDATE assignments SET stage_since = '2026-11-16T12:00:00', closed_at = NULL")
    r = api.get("/api/v2/assignments")
    assert r.status_code == 200 and data(r)["data"][0]["id"] == 1
    assert api.get("/api/v2/summary").status_code == 200


def test_clean_rows_are_not_flagged(tmp_path):
    from school_dashboard.assignments import repo
    st = repo.get_assignment(_conn(tmp_path, one()), 1)
    assert st is not None and st.data_problem is False


def test_one_corrupt_row_does_not_blank_the_list(api_factory):
    api = api_factory([*one(), _sam()])
    api.exec("UPDATE assignments SET due_date = '2025-09-31', stage_since = 'garbage' WHERE id = 1")
    listing = data(api.get("/api/v2/assignments"))["data"]
    by_id = {i["id"]: i for i in listing}
    assert sorted(by_id) == [1, 2]
    assert "data_problem" in by_id[1]["info"] and "data_problem" not in by_id[2]["info"]
    s = data(api.get("/api/v2/summary"))["data"]
    assert s["warnings"].count("1 assignment rows had unreadable dates") == 1


def test_summary_warns_about_a_bad_calendar_row_exactly_once(api_factory):
    api = api_factory(one())
    api.exec("INSERT INTO events (date, title, type, child) VALUES ('2025-09-31', 't', 'NO_SCHOOL', NULL)")
    r = api.get("/api/v2/summary")
    assert r.status_code == 200 and data(r)["data"]["warnings"].count(CAL) == 1
    assert api.get("/api/v2/assignments").status_code == 200


# ---- other boundaries ------------------------------------------------------------

@pytest.mark.parametrize("stamp", ["garbage", "", "2026-11-18T09:00:00"])
def test_outbox_rows_with_unreadable_or_naive_created_at_are_suppressed_not_raised(tmp_path, stamp):
    from school_dashboard.assignments import notify
    conn = _conn(tmp_path, one())
    rows = [{"id": 1, "created_at": stamp}]
    from school_dashboard.assignments import repo
    with repo.transaction(conn):
        repo.enqueue_push(conn, 1, "missing", NOW, "transition")
        conn.execute("UPDATE push_outbox SET created_at = ?", (stamp,))
    live, expired = notify._expire(conn, [{"id": 1, "created_at": stamp}], NOW)
    if stamp == "2026-11-18T09:00:00":
        assert (live, expired) == (rows, 0)
    else:
        assert live == [] and expired == 1
        assert conn.execute("SELECT status FROM push_outbox WHERE id = 1").fetchone()[0] == "suppressed"


def test_corrupt_aliases_json_degrades_everywhere(api_factory, caplog):
    api = api_factory(one(), setup={"class_settings": [{"kid": "Alex", "class_key": "math 6", "aliases": ["x"]}]})
    api.exec("UPDATE class_settings SET aliases = '{not json'")
    r = api.get("/api/v2/classes?kid=Alex")
    assert r.status_code == 200 and all(c["aliases"] == [] for c in data(r)["data"])
    from school_dashboard.assignments import ingest_kid
    assert ingest_kid.parse_aliases([("math 6", None, "{not json"), ("art", None, "5")]) == {}


@pytest.mark.parametrize("raw", ["2025-09-31", "Sept 31, 2025", "garbage"])
def test_adapter_due_dates_that_are_not_real_stay_undated_and_flagged(raw):
    from school_dashboard.assignments.adapters.base import parse_due
    assert parse_due(raw) == (None, True)


def test_cli_now_with_an_impossible_date_is_a_clear_exit_2(api_factory, capsys):
    from school_dashboard.assignments import cli as acli
    api_factory(one())
    ns = argparse.Namespace(assignments_command="digest", mode="night", now="2025-09-31T10:00:00", json=False)
    with pytest.raises(SystemExit) as exc:
        acli.cmd_assignments(ns)
    assert exc.value.code == 2 and "--now must be an ISO 8601" in capsys.readouterr().err


# ---- last line of defence: one kid never takes down the others ---------------------

def _boom_for_sam(real):
    def inner(rec, *a, **kw):
        if rec.state.kid == "Sam":
            raise RuntimeError("synthetic")
        return real(rec, *a, **kw)
    return inner


def test_summary_degrades_per_kid(api_factory, monkeypatch, caplog):
    from school_dashboard.assignments import readmodel
    api = api_factory([*one(), _sam()])
    monkeypatch.setattr(readmodel, "_item", _boom_for_sam(readmodel._item))
    caplog.set_level(logging.WARNING)
    r = api.get("/api/v2/summary")
    assert r.status_code == 200
    kids = {k["name"]: k for k in data(r)["data"]["kids"]}
    assert kids["Sam"]["error"] == "unavailable" and kids["Sam"]["counts"]["needs_action"] == 0
    assert kids["Sam"]["all_clear"] is False and "error" not in kids["Alex"]
    assert kids["Alex"]["counts"]["do_tonight"] == 1
    assert data(r)["data"]["warnings"].count("1 child could not be loaded") == 1
    assert any("RuntimeError" in x.getMessage() for x in caplog.records)
    assert "synthetic" not in caplog.text


def test_list_degrades_per_kid(api_factory, monkeypatch):
    from school_dashboard.assignments import readmodel
    api = api_factory([*one(), _sam()])
    monkeypatch.setattr(readmodel, "_item", _boom_for_sam(readmodel._item))
    r = api.get("/api/v2/assignments")
    assert r.status_code == 200 and [i["kid"] for i in data(r)["data"]] == ["Alex"]
    assert r.get_json()["meta"]["warnings"] == ["1 child could not be loaded"]


def test_digest_shows_an_unavailable_kid_and_the_others(api_factory):
    from school_dashboard.assignments import digest_v2
    summary = {"kids": [{"name": "Alex", "counts": {}, "all_clear": True}, {"name": "Sam", "error": "unavailable"}],
               "ingest_ok": True, "data_as_of": NOW.isoformat(), "unverified_sources": [], "warnings": []}
    text = digest_v2.build(summary, [], mode="night", now=NOW).text
    assert "Sam: could not be loaded" in text and "Alex: all clear" in text


@pytest.mark.parametrize("value", ["garbage", "2025-09-31T10:00:00", None, 5])
def test_digest_dates_that_are_not_real_are_omitted(value):
    from school_dashboard.assignments import digest_v2
    assert digest_v2._day(value) == ""
    summary = {"kids": [], "ingest_ok": True, "data_as_of": value, "unverified_sources": []}
    assert digest_v2.build(summary, [], mode="night", now=NOW).text.startswith("Data as of")
