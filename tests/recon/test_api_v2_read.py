"""GET routes of /api/v2 (Flask test client, synthetic data)."""
import json
from datetime import timedelta

import pytest

from recon.api_support import TOPIC, data, err, make_api, one, ps, sgy
from recon.s4_support import NOW


def test_config_always_answers(api_factory):
    api = api_factory(one())
    body = data(api.get("/api/v2/config"))
    assert body["ok"] is True and body["error"] is None
    cfg = body["data"]
    assert cfg["enabled"] is True and cfg["migrated"] is True and cfg["grace_default"] == 2
    assert cfg["kids"] == [{"name": "Alex", "colorIndex": 0}, {"name": "Sam", "colorIndex": 1}]


def test_config_when_disabled_and_not_migrated(api_factory):
    api = api_factory(enabled=False, migrated=False)
    cfg = data(api.get("/api/v2/config"))["data"]
    assert cfg["enabled"] is False and cfg["migrated"] is False


def test_config_without_db_file(api_factory):
    api = api_factory(enabled=True)
    import os
    os.remove(api.db)
    r = api.get("/api/v2/config")
    assert r.status_code == 200 and data(r)["data"]["migrated"] is False


def test_summary_counts_and_zero_kid(api_factory):
    api = api_factory(one())
    s = data(api.get("/api/v2/summary"))["data"]
    alex, sam = s["kids"]
    assert alex["name"] == "Alex" and alex["counts"]["do_tonight"] == 1 and alex["all_clear"] is False
    assert sam["name"] == "Sam" and sam["counts"]["do_tonight"] == 0 and sam["all_clear"] is True
    assert s["ingest_ok"] is True and s["generated_at"].startswith("2026-11-18T17:00")
    assert s["data_as_of"] is not None


def test_list_default_and_filters(api_factory):
    seed = [*one(), sgy("Quiz", "Math 6", "s2", due="2026-12-20"),
            {"kid": "Sam", "title": "Poster", "title_key": "poster", "class_name": "Art", "class_key": "art",
             "due_date": "2026-11-19", "stage": 2, "links": []},
            {"kid": "Alex", "title": "Chore", "title_key": "chore", "class_name": "", "class_key": "",
             "due_date": None, "kind": "task", "links": []}]
    api = api_factory(seed)
    body = data(api.get("/api/v2/assignments"))
    assert body["ok"] and len(body["data"]) == 4 and body["meta"]["count"] == 4
    ids = lambda q: [i["title"] for i in data(api.get("/api/v2/assignments" + q))["data"]]  # noqa: E731
    assert ids("?kid=Sam") == ["Poster"]
    assert "Essay" in ids("?bucket=do_tonight")
    assert ids("?stage=2") == ["Poster"]
    assert ids("?kind=task") == ["Chore"]
    assert "Quiz" in ids("?flag=upcoming")
    assert len(ids("?limit=2")) == 2 and len(ids("?limit=2&offset=3")) == 1 and ids("?offset=9") == []


def test_list_include_closed(api_factory):
    seed = [{"kid": "Alex", "title": "Old", "title_key": "old", "class_name": "M", "class_key": "m",
             "due_date": "2026-11-10", "stage": 4, "links": []}, *one()]
    api = api_factory(seed)
    assert [i["title"] for i in data(api.get("/api/v2/assignments"))["data"]] == ["Essay"]
    assert len(data(api.get("/api/v2/assignments?include_closed=1"))["data"]) == 2


@pytest.mark.parametrize("q,code", [
    ("limit=0", "invalid_query"), ("limit=501", "invalid_query"), ("offset=-1", "invalid_query"),
    ("bucket=bogus", "invalid_query"), ("stage=9", "invalid_query"), ("kid=Zed", "invalid_kid")])
def test_list_validation_errors(api_factory, q, code):
    err(api_factory(one()).get("/api/v2/assignments?" + q), 400, code)


def test_detail_with_attributed_events(api_factory):
    api = api_factory(one())
    api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": {"role": "parent", "name": "Dana"}})
    d = data(api.get("/api/v2/assignments/1"))["data"]
    assert d["id"] == 1 and d["stage"] == 2
    ev = d["events"][0]
    assert ev["kind"] == "stage" and ev["setter"] == "parent" and ev["actor"] == "Dana"
    assert ev["from"] == "1" and ev["to"] == "2"
    assert set(ev) == {"at", "setter", "actor", "kind", "from", "to", "detail"}


def test_detail_not_found_and_merged_away(api_factory):
    seed = [sgy("Essay", "Math 6", "s1"), ps("Essay", "Math 6", "p1")]
    api = api_factory(seed)
    err(api.get("/api/v2/assignments/99"), 404, "not_found")
    api.post("/api/v2/assignments/2/merge", {"into": 1, "actor": {"role": "parent", "name": "Dana"}})
    err(api.get("/api/v2/assignments/2"), 404, "not_found")
    assert len(data(api.get("/api/v2/assignments"))["data"]) == 1


def test_classes_get(api_factory):
    api = api_factory(one(), setup={"class_settings": [
        {"kid": "Alex", "class_key": "math 6", "grace_school_days": 4, "paper_only": 1}]})
    rows = data(api.get("/api/v2/classes?kid=Alex"))["data"]
    assert rows[0]["class_key"] == "math 6" and rows[0]["grace_school_days"] == 4 and rows[0]["paper_only"] is True
    err(api.get("/api/v2/classes"), 400, "invalid_kid")
    err(api.get("/api/v2/classes?kid=Zed"), 400, "invalid_kid")


def test_flag_on_read_follows_request_time_now(api_factory):
    api = api_factory(one())
    api.exec("UPDATE assignments SET flag = 'upcoming'")  # stale stored value on purpose
    assert data(api.get("/api/v2/assignments/1"))["data"]["flag"] == "do_tonight"
    api.clock["now"] = NOW + timedelta(days=3)
    assert data(api.get("/api/v2/assignments/1"))["data"]["flag"] == "missing"


def test_unverified_when_ingest_stale(api_factory):
    api = api_factory(one(), meta="stale")
    item = data(api.get("/api/v2/assignments/1"))["data"]
    assert item["unverified"] is True and "recon_ingest" in item["unverified_sources"]
    s = data(api.get("/api/v2/summary"))["data"]
    assert s["ingest_ok"] is False and s["warnings"]


def test_unverified_when_no_sync_meta(api_factory):
    api = api_factory(one(), meta="none")
    assert data(api.get("/api/v2/assignments/1"))["data"]["unverified"] is True


def test_roster_absent_uses_assignment_kids(api_factory):
    api = api_factory(one(), roster=None)
    assert data(api.get("/api/v2/assignments?kid=Alex"))["ok"]
    err(api.get("/api/v2/assignments?kid=Sam"), 400, "invalid_kid")
    assert [k["name"] for k in data(api.get("/api/v2/config"))["data"]["kids"]] == []


def test_forbidden_fields_never_leak(api_factory):
    seed = [{**sgy("Essay", "Math 6", "EXTKEY-123", due="2026-11-19"), "links": [
        {"source": "schoology", "external_key": "EXTKEY-123", "norm_status": "open", "last_miss_hash": "MISSHASH",
         "url": "https://user:pw@sgy.example.invalid/a?x=1"},
        {"source": "powerschool", "external_key": "EXTKEY-456", "norm_status": "open",
         "url": "https://sgy.example.invalid/b?api_key=ZZZ"},
        {"source": "ixl", "external_key": "EXTKEY-789", "norm_status": "open",
         "url": "https://sgy.example.invalid/ok?page=2"}]}]
    api = api_factory(seed)
    api.exec("UPDATE source_links SET raw_json = ?", ('{"secret": "RAWSECRET"}',))
    texts = [api.get(p).get_data(as_text=True) for p in (
        "/api/v2/config", "/api/v2/summary", "/api/v2/assignments", "/api/v2/assignments/1",
        "/api/v2/classes?kid=Alex")]
    blob = "\n".join(texts)
    for bad in ("raw_json", "RAWSECRET", "external_key", "EXTKEY", "last_miss_hash", "MISSHASH", "pw@", "api_key",
                "ZZZ", TOPIC, "user:pw"):
        assert bad not in blob, bad
    item = data(api.get("/api/v2/assignments/1"))["data"]
    urls = [ln["url"] for ln in item["links"]]
    assert urls == [None, None, "https://sgy.example.invalid/ok?page=2"]


def test_title_with_html_and_control_chars(api_factory):
    seed = [sgy("<script>alert(\"x\")</script>\x00\x07 Q\n2", "Math 6", "s1")]
    api = api_factory(seed)
    r = api.get("/api/v2/assignments/1")
    assert r.content_type.startswith("application/json")
    title = json.loads(r.get_data(as_text=True))["data"]["title"]
    assert title == '<script>alert("x")</script> Q 2'
    assert "\\u0000" not in r.get_data(as_text=True) and "\x00" not in r.get_data(as_text=True)


def test_no_store_on_every_response(api_factory):
    api = api_factory(one())
    for path in ("/api/v2/config", "/api/v2/summary", "/api/v2/assignments", "/api/v2/assignments/1",
                 "/api/v2/assignments/99", "/api/v2/classes", "/api/v2/nope", "/api/v2/assignments?limit=0"):
        assert api.get(path).headers["Cache-Control"] == "no-store", path
    assert api.post("/api/v2/summary", {}).headers["Cache-Control"] == "no-store"  # 405


def test_disabled_404_except_config(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, one(), enabled=False)
    for path in ("/api/v2/summary", "/api/v2/assignments", "/api/v2/assignments/1", "/api/v2/classes?kid=Alex"):
        r = api.get(path)
        err(r, 404, "recon_disabled")
        assert r.headers["Cache-Control"] == "no-store"
    assert api.get("/api/v2/config").status_code == 200


def test_not_migrated_409_and_missing_db(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, migrated=False)
    for path in ("/api/v2/summary", "/api/v2/assignments", "/api/v2/assignments/1", "/api/v2/classes?kid=Alex"):
        err(api.get(path), 409, "not_migrated")
    import os
    os.remove(api.db)
    err(api.get("/api/v2/summary"), 409, "not_migrated")


def test_every_request_closes_its_connection(api_factory, monkeypatch):
    from school_dashboard.assignments import schema
    opened = []
    real = schema.connect

    def spy(*a, **kw):
        c = real(*a, **kw)
        opened.append(c)
        return c
    monkeypatch.setattr(schema, "connect", spy)
    api = api_factory(one())
    opened.clear()
    api.get("/api/v2/summary")
    api.get("/api/v2/assignments/1")
    api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": {"role": "parent", "name": "D"}})
    api.get("/api/v2/assignments/99")
    assert opened
    import sqlite3
    for c in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")


def test_unexpected_exception_is_500_internal_and_logged_by_type(api_factory, monkeypatch, caplog):
    from school_dashboard.assignments import readmodel

    def boom(*a, **kw):
        raise RuntimeError("SECRET Essay title")
    monkeypatch.setattr(readmodel, "build_summary", boom)
    api = api_factory(one())
    with caplog.at_level("ERROR"):
        r = api.get("/api/v2/summary")
    err(r, 500, "internal")
    assert "SECRET" not in r.get_data(as_text=True) and "Traceback" not in r.get_data(as_text=True)
    assert "RuntimeError" in caplog.text and "SECRET" not in caplog.text
