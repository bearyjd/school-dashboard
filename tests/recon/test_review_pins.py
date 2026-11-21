"""Pins for behaviours that were only protected by luck: per-kid isolation, naive-datetime zone, calendar types."""
import sqlite3
from datetime import date

import pytest

from recon.support import local, mk_state
from school_dashboard.assignments import flags, readmodel, repo, schooldays
from school_dashboard.assignments.health import IngestHealth
from school_dashboard.assignments.model import AssignmentRecord

OK = IngestHealth(True, local(2026, 11, 18, 17), None)
FRI = local(2026, 11, 20, 17)


def _summary(rows, **over):
    return readmodel.build_summary(
        rows, {}, kids=("Alex", "Sam"), warnings=(), now=FRI, no_school_by_kid={}, grace_by_class={},
        stale_sources=frozenset(), ingest=OK, **over)


def _rows():
    return [AssignmentRecord(mk_state(id=1, kid="Alex", due_date="2026-11-21")),
            AssignmentRecord(mk_state(id=2, kid="Sam", due_date="2026-11-21"))]


def _kids(out):
    return {k["name"]: k for k in out["kids"]}


def test_one_kid_failing_to_build_items_keeps_the_others(monkeypatch):
    real = readmodel._item

    def flaky(rec, links, ctx):
        if rec.state.kid == "Alex":
            raise RuntimeError("boom")
        return real(rec, links, ctx)
    monkeypatch.setattr(readmodel, "_item", flaky)
    kids = _kids(_summary(_rows()))
    assert kids["Alex"]["error"] == "unavailable" and not kids["Alex"]["all_clear"]
    assert "error" not in kids["Sam"] and kids["Sam"]["counts"]["do_tonight"] == 1


def test_one_kid_failing_to_summarise_keeps_the_others(monkeypatch):
    real = readmodel._kid_summary

    def flaky(name, pairs, ingest):
        if name == "Alex":
            raise RuntimeError("boom")
        return real(name, pairs, ingest)
    monkeypatch.setattr(readmodel, "_kid_summary", flaky)
    kids = _kids(_summary(_rows()))
    assert kids["Alex"]["error"] == "unavailable" and not kids["Alex"]["all_clear"]
    assert "error" not in kids["Sam"] and kids["Sam"]["counts"]["do_tonight"] == 1


def _stored(**over):
    row = dict(id=1, kid="Alex", class_name="Math 6", class_key="math 6", title="T", title_key="t", due_date=None,
               kind="assignment", stage=2, stage_since="2026-11-20T02:00:00", received_evidence=None,
               portal_status="none", portal_conflict=0, closed_at=None, paper_only_flag=0,
               created_at="2026-11-20T02:00:00", updated_at="2026-11-20T02:00:00")
    return {**row, **over}


@pytest.mark.parametrize("tz,expected", [("America/New_York", "waiting_on_teacher"), ("UTC", "done_not_received")])
def test_naive_stored_datetimes_are_read_in_the_configured_zone(monkeypatch, tz, expected):
    # 02:00 naive is 11-20 in New York but 11-19 (21:00 local) when read as UTC -> different grace outcome today
    monkeypatch.setenv("TZ", tz)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    values = _stored()
    row = conn.execute("SELECT " + ", ".join(f":{k} AS {k}" for k in values), values).fetchone()
    state = repo._to_state(row)
    assert flags._flag(state, FRI, frozenset(), 0) == expected


@pytest.mark.parametrize("kind", ["HALF_DAY", "EARLY_RELEASE"])
def test_half_and_early_days_are_still_school_days(kind):
    rows = [{"date": "2026-11-23", "type": kind, "child": None}]
    no_school = schooldays.no_school_dates(rows, "Alex")
    assert no_school == frozenset()
    assert schooldays.next_school_day(date(2026, 11, 20), no_school) == date(2026, 11, 23)
