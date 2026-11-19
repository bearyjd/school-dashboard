"""Gates on /api/v2 writes: maintenance, flag, migration, content type, size, shape, busy."""
import json

import pytest

from recon.api_support import ACTOR, make_api, one, ps, sgy, err

SEED = [sgy("Essay", "Math 6", "s1", stage=2), ps("Essay", "Math 6", "p1")]
VALID = {
    ("post", "/api/v2/assignments/1/stage"): {"action": "done", "actor": ACTOR},
    ("post", "/api/v2/assignments/1/teacher-confirmed"): {"note": "Mrs R confirmed", "actor": ACTOR},
    ("post", "/api/v2/assignments/1/merge"): {"into": 2, "actor": ACTOR},
    ("post", "/api/v2/assignments/1/split"): {"link_id": 1, "actor": ACTOR},
    ("post", "/api/v2/kids/Alex/remerge"): {"actor": ACTOR},
    ("put", "/api/v2/classes"): {"kid": "Alex", "class_name": "Math 6", "actor": ACTOR},
}
ROUTES = list(VALID)
IDS = [f"{m}:{p}" for m, p in ROUTES]


def call(api, route, body=None, **kw):
    method, path = route
    return api.send(method, path, VALID[route] if body is None else body, **kw)


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_maintenance_blocks_every_write_but_gets_answer(api_factory, route):
    from school_dashboard import maintenance
    api = api_factory(SEED)
    maintenance.enable("test")
    r = call(api, route)
    assert r.status_code == 503 and r.get_json() == {"error": "maintenance"} and r.headers["Retry-After"] == "60"
    assert api.q("SELECT COUNT(*) AS n FROM assignment_events")[0]["n"] == 0
    for path in ("/api/v2/config", "/api/v2/summary", "/api/v2/assignments", "/api/v2/assignments/1",
                 "/api/v2/classes?kid=Alex"):
        assert api.get(path).status_code == 200, path


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_disabled_flag_is_404_on_writes(tmp_path, monkeypatch, route):
    api = make_api(tmp_path, monkeypatch, SEED, enabled=False)
    err(call(api, route), 404, "recon_disabled")
    assert api.q("SELECT COUNT(*) AS n FROM assignment_events")[0]["n"] == 0


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_not_migrated_is_409_on_writes(tmp_path, monkeypatch, route):
    api = make_api(tmp_path, monkeypatch, migrated=False)
    err(call(api, route), 409, "not_migrated")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None])
def test_wrong_content_type_is_415(api_factory, route, ctype):
    api = api_factory(SEED)
    err(call(api, route, ctype=ctype), 415, "unsupported_media_type")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_json_with_charset_is_accepted_type(api_factory, route):
    api = api_factory(SEED)
    r = call(api, route, ctype="application/json; charset=utf-8")
    assert r.status_code != 415


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_oversized_body_is_413(api_factory, route):
    api = api_factory(SEED)
    body = {**VALID[route], "pad": "x" * 9000}
    err(call(api, route, body), 413, "payload_too_large")
    err(call(api, route, raw=b" " * 9000 + b"{}"), 413, "payload_too_large")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
@pytest.mark.parametrize("raw", ["[]", "1", '"x"', "null", "not json", "{", ""])
def test_non_object_json_is_400(api_factory, route, raw):
    api = api_factory(SEED)
    r = call(api, route, raw=raw)
    assert r.status_code == 400 and r.get_json()["error"] in ("invalid_json", "invalid_body")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_unknown_keys_rejected(api_factory, route):
    api = api_factory(SEED)
    err(call(api, route, {**VALID[route], "zzz": 1}), 400, "unknown_field")
    bad_actor = {**ACTOR, "extra": 1}
    err(call(api, route, {**VALID[route], "actor": bad_actor}), 400, "unknown_field")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
@pytest.mark.parametrize("actor", [None, "Dana", [], {"role": "admin", "name": "D"}, {"role": "parent", "name": ""},
                                   {"role": "parent", "name": "x" * 41}, {"role": True, "name": "D"},
                                   {"role": "parent", "name": 3}, {"name": "D"}, {"role": "parent"}])
def test_bad_or_missing_actor_is_400(api_factory, route, actor):
    api = api_factory(SEED)
    body = {k: v for k, v in VALID[route].items() if k != "actor"}
    if actor is not None:
        body["actor"] = actor
    err(call(api, route, body), 400, "invalid_actor")


FIELD_CASES = [
    (("post", "/api/v2/assignments/1/stage"), "action", [True, 1, None, "bogus", "", "DONE", ["done"]]),
    (("post", "/api/v2/assignments/1/teacher-confirmed"), "note", [1, None, "ab", "x" * 501, " \x00 ", True]),
    (("post", "/api/v2/assignments/1/merge"), "into", [True, "2", 2.0, None, 0, -1, [2], 2 ** 70]),
    (("post", "/api/v2/assignments/1/split"), "link_id", [True, "1", 1.5, None, 0, -1, [1], 2 ** 70]),
    (("put", "/api/v2/classes"), "class_name", [None, 5, "", "  ", "x" * 121]),
    (("put", "/api/v2/classes"), "grace_school_days", [True, "3", 11, -1, 2.5]),
    (("put", "/api/v2/classes"), "paper_only", [1, "yes", None]),
    (("put", "/api/v2/classes"), "aliases", ["ELA", [1], [""], ["x"] * 21, ["x" * 101]]),
    (("put", "/api/v2/classes"), "teacher_name", [5, "x" * 121]),
    (("put", "/api/v2/classes"), "teacher_email", [5, "x" * 201]),
]


@pytest.mark.parametrize("route,field,bad", [(r, f, b) for r, f, bs in FIELD_CASES for b in bs])
def test_bad_field_values_are_400_and_change_nothing(api_factory, route, field, bad):
    api = api_factory(SEED)
    err(call(api, route, {**VALID[route], field: bad}), 400, "invalid_field")
    assert api.q("SELECT COUNT(*) AS n FROM assignment_events")[0]["n"] == 0


@pytest.mark.parametrize("route", [("put", "/api/v2/classes")])
@pytest.mark.parametrize("kid", [None, 5, "", "Zed", "alex", ["Alex"]])
def test_off_roster_or_bad_kid_in_body(api_factory, route, kid):
    api = api_factory(SEED)
    err(call(api, route, {**VALID[route], "kid": kid}), 400, "invalid_kid")


def test_off_roster_kid_in_path(api_factory):
    err(api_factory(SEED).post("/api/v2/kids/Zed/remerge", {"actor": ACTOR}), 400, "invalid_kid")


def test_kid_not_on_roster_but_present_in_assignments_is_rejected_when_roster_configured(api_factory):
    api = api_factory([{**sgy("Essay", "Math 6", "s1"), "kid": "Jo"}])
    err(api.post("/api/v2/kids/Jo/remerge", {"actor": ACTOR}), 400, "invalid_kid")


def test_without_roster_kid_must_exist_in_assignments(api_factory):
    api = api_factory(one(), roster=None)
    assert api.post("/api/v2/kids/Alex/remerge", {"actor": ACTOR}).status_code == 200
    err(api.post("/api/v2/kids/Sam/remerge", {"actor": ACTOR}), 400, "invalid_kid")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_busy_is_503_with_retry_after(api_factory, monkeypatch, route):
    from school_dashboard.assignments import schema
    api = api_factory(SEED)
    monkeypatch.setattr(schema, "BUSY_TIMEOUT_MS", 50)
    holder = api.conn()
    holder.execute("BEGIN IMMEDIATE")
    try:
        r = call(api, route)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    err(r, 503, "busy")
    assert r.headers["Retry-After"] == "2" and r.headers["Cache-Control"] == "no-store"
    assert r.status_code == 503 and json.loads(r.get_data(as_text=True))["error"] == "busy"


def test_methods_not_allowed(api_factory):
    api = api_factory(SEED)
    assert api.get("/api/v2/assignments/1/stage").status_code == 405
    assert api.client.delete("/api/v2/assignments/1").status_code == 405
