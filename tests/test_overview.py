from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from school_dashboard.overview import build_overview

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 6, 16, 30, tzinfo=NY)  # Friday, after the 14:30 sync


def _roster(*names):
    return [{"id": n.lower(), "name": n, "grade": "1st", "school": "S"} for n in names]


def _iso(delta_hours):
    return (NOW - timedelta(hours=delta_hours)).isoformat()


def _build(state=None, roster=("Alpha", "Beta", "Gamma"), meta=None, gc=None, school=(), **kw):
    return build_overview(state or {}, [], gc or {}, list(school), meta or {},
                          _roster(*roster), NOW, NY, **kw)


def test_shape_and_kid_colour_order():
    out = _build()
    assert set(out) == {"generated_at", "kids", "items", "events", "freshness", "warnings"}
    assert out["kids"] == [{"name": "Alpha", "colorIndex": 0}, {"name": "Beta", "colorIndex": 1},
                           {"name": "Gamma", "colorIndex": 2}]
    assert [k["colorIndex"] for k in _build(roster=("A", "B", "C", "D"))["kids"]] == [0, 1, 2, 3]
    assert [k["colorIndex"] for k in _build(roster=("Solo",))["kids"]] == [0]
    assert set(out["freshness"]) == {"ixl", "sgy", "gc", "powerschool"}


def test_stale_source_flagged():
    state = {"powerschool": {"Alpha": {"missing": [{"title": "Quiz", "due": "2026-11-01"}]}}}
    meta = {
        "powerschool": {"last_run": _iso(30), "last_ok": _iso(30), "last_result": "ok"},
        "sgy": {"last_run": _iso(1), "last_ok": _iso(40), "last_result": "error"},
        "ixl": {"last_run": _iso(2), "last_ok": _iso(2), "last_result": "ok"},
    }
    out = _build(state, meta=meta)
    fr = out["freshness"]
    assert fr["powerschool"]["stale"] is True and fr["powerschool"]["failing"] is False
    assert fr["sgy"]["failing"] is True
    assert fr["ixl"]["stale"] is False and fr["ixl"]["failing"] is False
    assert fr["powerschool"]["age_hours"] == 30.0
    assert [i["source"] for i in out["items"]] == ["powerschool"]


def test_truncation_warning():
    state = {"powerschool": {"Alpha": {"missing": [
        {"title": f"m{i}", "due": "2026-10-01"} for i in range(501)]}}}
    out = _build(state)
    assert len([i for i in out["items"] if i["status"] == "missing"]) == 500
    assert any("missing" in w for w in out["warnings"])


def test_events_merged_and_windowed():
    gc = {"teams": [{"team_name": "Lions", "child": "Alpha", "schedule": [
        {"date": "2026-11-08", "time": "09:00", "opponent": "Hawks"},
        {"date": "2026-11-30", "time": "09:00"}]}]}
    school = [{"kids": [], "title": "No School", "date": "2026-11-09", "time": "", "type": "",
               "location": "", "source": "school", "scheduleSource": None, "team": None}]
    out = _build(gc=gc, school=school)
    assert [e["date"] for e in out["events"]] == ["2026-11-08", "2026-11-09"]
    assert [e["date"] for e in _build(gc=gc, days=1)["events"]] == []
