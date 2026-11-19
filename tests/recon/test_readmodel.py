"""Read model: flags on read, unverified, shapes, URL filter, practice, summary."""
import json
from datetime import date

import pytest

from recon.report_support import sgy
from recon.s4_support import NOW, open_conn
from recon.support import local, mk_link, mk_state
from school_dashboard.assignments import readmodel, repo
from school_dashboard.assignments.health import IngestHealth
from school_dashboard.assignments.model import AssignmentRecord, LinkRecord

OK = IngestHealth(True, NOW, None)
BAD = IngestHealth(False, None, "error")
BASE = dict(now=NOW, no_school_by_kid={}, grace_by_class={}, stale_sources=frozenset(), ingest=OK)


def rec(i, dup=False, **o):
    return AssignmentRecord(mk_state(id=i, **o), dup)


def lk(i, raw_status="", **o):
    return LinkRecord(i, raw_status, mk_link(**o))


def build(rows, links=None, **o):
    return readmodel.build_list(rows, links or {}, **{**BASE, "filters": readmodel.ListFilters(), **o})


def item(row, links=None, **o):
    return build([row], links, **o)[0]


def test_item_shape_and_label():
    it = item(rec(1, due_date="2026-11-20", stage=2), {1: [lk(7, "Open", external_key="SECRETKEY", last_miss_hash="HASHY",
                                                         url="https://sgy.example.invalid/a")]})
    assert set(it) == {"id", "kid", "title", "class", "due", "kind", "stage", "stage_label", "flag", "bucket", "info",
                       "unverified", "unverified_sources", "portal_status", "conflict", "received_evidence",
                       "possible_duplicate", "links"}
    assert (it["stage_label"], it["due"], it["class"]) == ("Done at home", "2026-11-20", "Math 6")
    assert set(it["links"][0]) == {"id", "source", "status", "raw_status", "score", "live", "url"}
    text = json.dumps(it)
    assert "SECRETKEY" not in text and "HASHY" not in text and "raw_json" not in text


@pytest.mark.parametrize("stage,label", [(1, "Assigned"), (2, "Done at home"), (3, "Turned in"), (4, "Received")])
def test_stage_labels(stage, label):
    assert item(rec(1, stage=stage, due_date="2026-11-30"), {1: [lk(1)]})["stage_label"] == label


def test_paper_stage_3_is_marked_unconfirmed():
    it = item(rec(1, stage=3, due_date="2026-11-30"))
    assert it["stage_label"] == "Turned in (unconfirmed)" and "unconfirmed_paper" in it["info"]


def test_title_is_cleaned_plain_text():
    assert item(rec(1, title="<b>Hi</b>\x00 there\n"))["title"] == "<b>Hi</b> there"


@pytest.mark.parametrize("url,ok", [
    ("https://a.example.invalid/x?y=1", True), ("http://a.example.invalid/", True),
    ("https://user:pw@a.example.invalid/", False), ("https://user@a.example.invalid/", False),
    ("javascript:alert(1)", False), ("ftp://a.example.invalid/", False), ("file:///etc/passwd", False),
    ("https://a.example.invalid/?Token=abc", False), ("https://a.example.invalid/?x=1&API_KEY=z", False),
    ("https://a.example.invalid/?session=1", False), ("https://a.example.invalid/?sig=1", False),
    ("https://a.example.invalid/?signature=1", False), ("https://a.example.invalid/?password=1", False),
    ("https://a.example.invalid/?auth=1", False), ("https://a.example.invalid/?secret=1", False),
    ("https://a.example.invalid/#access_token=abc", False), (None, False), ("", False), ("not a url", False)])
def test_url_filter(url, ok):
    out = item(rec(1), {1: [lk(1, url=url)]})["links"][0]["url"]
    assert (out == url) if ok else out is None


def test_flag_is_computed_on_read_not_taken_from_the_stored_column(tmp_path):
    c = open_conn(tmp_path, [sgy("Essay", "Math 6", "s1", stage=2, stage_since="2026-11-17T12:00:00-05:00")])
    c.execute("UPDATE assignments SET flag = 'waiting_on_teacher' WHERE id = 1")
    rows = repo.assignment_records(c)
    links = repo.link_records(c, [1])
    early = readmodel.build_list(rows, links, **{**BASE, "filters": readmodel.ListFilters()})[0]
    late = readmodel.build_list(rows, links, **{**BASE, "now": local(2026, 12, 20, 9),
                                                "filters": readmodel.ListFilters()})[0]
    assert (early["flag"], late["flag"]) == ("waiting_on_teacher", "done_not_received")


def test_unverified_when_ingest_is_not_ok_or_source_is_stale():
    row = rec(1, due_date="2026-11-30")
    links = {1: [lk(1)]}
    bad = item(row, links, ingest=BAD)
    assert bad["unverified"] and "recon_ingest" in bad["unverified_sources"]
    stale = item(row, links, stale_sources=frozenset({"schoology"}))
    assert stale["unverified"] and stale["unverified_sources"] == ["schoology"]
    assert not item(row, links)["unverified"]


@pytest.mark.parametrize("raw,bucket", [("3 remaining", "do_tonight"), ("12 remaining", "do_tonight"),
                                        ("0 remaining", None), ("garbage", None), ("", None), (None, None)])
def test_practice_rows(raw, bucket):
    it = item(rec(1, kind="practice", class_name=None, class_key=""), {1: [lk(1, raw, source="ixl")]})
    assert (it["kind"], it["bucket"]) == ("practice", bucket)


def test_possible_duplicate_comes_from_the_record():
    assert item(rec(1, dup=True))["possible_duplicate"] is True and item(rec(1))["possible_duplicate"] is False


def test_filters_and_paging():
    rows = [rec(1, due_date="2026-11-10"), rec(2, kid="Sam", due_date="2026-11-30"),
            rec(3, stage=4, due_date="2026-11-30"), rec(4, kind="task", due_date="2026-11-19", class_key="")]
    f = lambda **k: [i["id"] for i in build(rows, filters=readmodel.ListFilters(**k))]
    assert f() == [1, 4, 2]
    assert f(include_closed=True) == [1, 4, 2, 3] or sorted(f(include_closed=True)) == [1, 2, 3, 4]
    assert f(kid="Sam") == [2] and f(kind="task") == [4] and f(flag="missing") == [1]
    assert f(bucket="needs_action") == [1] and f(stage=4, include_closed=True) == [3]
    assert f(limit=1) == [1] and f(limit=1, offset=1) == [4]


def test_detail_adds_events_newest_first_capped():
    events = [{"at": f"2026-11-{d:02d}", "setter": "parent", "actor": "Dana", "kind": "stage", "from_value": "1",
               "to_value": "2", "detail": None} for d in range(1, 29)] * 8
    out = readmodel.build_detail(rec(1), [], events, **BASE)
    assert len(out["events"]) == 200 and out["events"][0]["at"] == events[-1]["at"]
    assert set(out["events"][0]) == {"at", "setter", "actor", "kind", "from", "to", "detail"}


def summary(rows, links=None, **o):
    return readmodel.build_summary(rows, links or {}, kids=("Alex", "Sam"), warnings=("w1",),
                                   **{k: v for k, v in {**BASE, **o}.items()})


def test_summary_counts_roster_kids_and_all_clear():
    rows = [rec(1, due_date="2026-11-10"), rec(2, due_date="2026-11-19"), rec(3, stage=2, due_date="2026-11-30"),
            rec(4, kind="practice", class_key="", class_name=None)]
    out = summary(rows, {4: [lk(4, "3 remaining", source="ixl")]})
    alex, sam = out["kids"]
    assert (alex["name"], sam["name"]) == ("Alex", "Sam")
    assert alex["counts"] == {"do_tonight": 1, "waiting_on_teacher": 1, "needs_action": 1,
                              "needs_action_detail": {"done_not_received": 0, "disputed": 0, "missing": 1}}
    assert alex["all_clear"] is False and [p["id"] for p in alex["practice"]] == [4]
    assert sam["counts"]["needs_action"] == 0 and sam["all_clear"] is True and sam["practice"] == []
    assert out["ingest_ok"] is True and out["warnings"][0] == "w1"
    assert out["data_as_of"] == NOW.isoformat() and out["generated_at"] == NOW.isoformat()


def test_summary_when_ingest_is_down():
    out = summary([rec(1, due_date="2026-11-30")], {1: [lk(1)]}, ingest=BAD)
    assert out["ingest_ok"] is False and out["data_as_of"] is None and "recon_ingest" in out["unverified_sources"]
    assert len(out["warnings"]) >= 2


def test_summary_is_json_serialisable_and_leaks_nothing():
    out = summary([rec(1, due_date="2026-11-10")], {1: [lk(1, external_key="SECRETKEY")]})
    assert "SECRETKEY" not in json.dumps(out)
