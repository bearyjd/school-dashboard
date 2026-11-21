"""S4 review round (API): write token, async push, framework errors, list total, summary, partial class PUT."""
import json
import logging
import sqlite3
import threading
import time

import pytest

from recon.api_support import ACTOR, TOKEN, data, err, one, ps, sgy
from recon.s4_support import NOW

PAIR = [sgy("Essay", "Math 6", "s1"), ps("Essay", "Math 6", "p1")]
WRITES = [
    ("post", "/api/v2/assignments/1/stage", {"action": "done", "actor": ACTOR}),
    ("post", "/api/v2/assignments/1/teacher-confirmed", {"note": "Teacher said ok", "actor": ACTOR}),
    ("post", "/api/v2/assignments/1/merge", {"into": 2, "actor": ACTOR}),
    ("post", "/api/v2/assignments/1/split", {"link_id": 1, "actor": ACTOR}),
    ("post", "/api/v2/kids/Alex/remerge", {"actor": ACTOR}),
    ("put", "/api/v2/classes", {"kid": "Alex", "class_name": "Math 6", "actor": ACTOR}),
]
ids = [f"{m}-{p.rsplit('/', 1)[-1]}" for m, p, _ in WRITES]


def call(api, route, **kw):
    method, path, body = route
    return getattr(api, method)(path, body, **kw)


def events(api):
    return api.q("SELECT COUNT(*) AS n FROM assignment_events")[0]["n"]


# ---- S1 ----
@pytest.mark.parametrize("route", WRITES, ids=ids)
@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN + "x", TOKEN[:-1]])
def test_missing_or_wrong_token_is_401_and_changes_nothing(api_factory, route, token):
    api = api_factory(PAIR)
    err(call(api, route, token=token), 401, "bad_token")
    assert events(api) == 0


@pytest.mark.parametrize("route", WRITES, ids=ids)
def test_correct_token_passes_the_gate(api_factory, route):
    api = api_factory(PAIR)
    assert call(api, route).status_code not in (401, 429, 501)


@pytest.mark.parametrize("route", WRITES, ids=ids)
@pytest.mark.parametrize("env", [None, "short-token-31-chars-aaaaaaaaaa"])
def test_unconfigured_or_short_token_is_501(api_factory, monkeypatch, route, env):
    api = api_factory(PAIR)
    if env is None:
        monkeypatch.delenv("RECON_WRITE_TOKEN")
    else:
        assert len(env) == 31
        monkeypatch.setenv("RECON_WRITE_TOKEN", env)
    err(call(api, route, token=env), 501, "write_token_not_configured")
    assert events(api) == 0


@pytest.mark.parametrize("route", WRITES, ids=ids)
def test_open_mode_needs_no_token(api_factory, monkeypatch, route):
    api = api_factory(PAIR)
    monkeypatch.setenv("RECON_WRITE_OPEN", "1")
    monkeypatch.delenv("RECON_WRITE_TOKEN")
    assert call(api, route, token=None).status_code not in (401, 429, 501)


def test_open_mode_wins_over_a_configured_token(api_factory, monkeypatch):
    api = api_factory(one())
    monkeypatch.setenv("RECON_WRITE_OPEN", "yes")
    assert api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": ACTOR}, token="nope").status_code == 200


@pytest.mark.parametrize("route", WRITES, ids=ids)
def test_token_is_checked_before_the_body_is_read(api_factory, route):
    api = api_factory(PAIR)
    method, path, _ = route
    err(api.send(method, path, raw="{not json", ctype="text/plain", token="wrong"), 401, "bad_token")
    err(api.send(method, path, raw=b"x" * 20000, token=None), 401, "bad_token")


def test_disabled_flag_is_checked_before_the_token(api_factory):
    api = api_factory(PAIR, enabled=False)
    err(api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": ACTOR}, token=None), 404, "recon_disabled")


def test_failures_are_limited_per_client_and_a_correct_token_is_never_blocked(api_factory):
    from web import app as webapp
    api = api_factory(one())
    route = WRITES[0]
    for _ in range(10):
        err(call(api, route, token="bad"), 401, "bad_token")
    r = call(api, route, token="bad")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert call(api, route).status_code == 200
    assert len(webapp._sync_failures) == 0  # the same limiter /api/sync uses; success resets it


def test_bad_v2_token_counts_in_the_sync_limiter(api_factory):
    from web import app as webapp
    api = api_factory(one())
    call(api, WRITES[0], token="bad")
    assert len(webapp._sync_failures) == 1


def test_reads_stay_open_and_the_token_is_never_returned(api_factory, monkeypatch):
    api = api_factory(PAIR)
    for path in ("/api/v2/config", "/api/v2/summary", "/api/v2/assignments", "/api/v2/assignments/1",
                 "/api/v2/classes?kid=Alex"):
        r = api.get(path)
        assert r.status_code == 200 and TOKEN.encode() not in r.data
    cfg = data(api.get("/api/v2/config"))["data"]
    assert cfg["write_auth"] == "token"
    monkeypatch.setenv("RECON_WRITE_OPEN", "1")
    assert data(api.get("/api/v2/config"))["data"]["write_auth"] == "open"
    monkeypatch.delenv("RECON_WRITE_OPEN")
    monkeypatch.delenv("RECON_WRITE_TOKEN")
    assert data(api.get("/api/v2/config"))["data"]["write_auth"] == "unconfigured"


def test_warnings_are_logged_once_without_the_token(api_factory, monkeypatch, caplog):
    api = api_factory(one())
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("RECON_WRITE_TOKEN")
    for _ in range(3):
        call(api, WRITES[0], token=None)
    monkeypatch.setenv("RECON_WRITE_TOKEN", "short")
    call(api, WRITES[0], token="short")
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and "RECON_WRITE_TOKEN" in r.getMessage()]
    assert len(warns) == 1 and "short" not in warns[0].getMessage().replace("RECON_WRITE_TOKEN", "")
    caplog.clear()
    monkeypatch.setenv("RECON_WRITE_OPEN", "1")
    for _ in range(3):
        call(api, WRITES[0], token=None)
    opens = [r for r in caplog.records if r.levelno == logging.WARNING and "RECON_WRITE_OPEN" in r.getMessage()]
    assert len(opens) == 1


# ---- S2b ----
def _pending(api, flag, hours_ago=1):
    ts = (NOW.replace(hour=16)).isoformat()
    api.exec("INSERT INTO push_outbox (assignment_id, flag, occurrence_at, reason, status, created_at)"
             " VALUES (1, ?, ?, 'transition', 'pending', ?)", (flag, ts, ts))


def _status(api):
    return [(r["flag"], r["status"]) for r in api.q("SELECT flag, status FROM push_outbox ORDER BY id")]


def test_push_is_sent_off_the_request_thread_and_only_one_sender_runs(api_factory, monkeypatch):
    from school_dashboard.assignments import notify
    from web import api_v2_push
    api = api_factory(one())
    monkeypatch.setenv("RECON_PUSH_ENABLED", "1")
    started, release, calls = threading.Event(), threading.Event(), []

    def fake(topic, message, title):
        calls.append(threading.current_thread())
        started.set()
        release.wait(5)

    monkeypatch.setattr(notify, "default_send", fake)
    _pending(api, "missing")
    t0 = time.monotonic()
    assert api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": ACTOR}).status_code == 200
    assert time.monotonic() - t0 < 2.5 and not release.is_set()
    assert started.wait(3) and calls[0] is not threading.main_thread()
    _pending(api, "disputed")
    assert api.post("/api/v2/assignments/1/stage", {"action": "undo", "actor": ACTOR}).status_code == 200
    time.sleep(0.2)
    assert len(calls) == 1  # the busy sender is not joined by a second one
    release.set()
    assert api_v2_push.wait_idle(5)
    assert len(calls) == 1 and ("disputed", "pending") in _status(api)


def test_a_failing_sender_never_breaks_the_route_or_leaves_the_lock(api_factory, monkeypatch, caplog):
    from school_dashboard.assignments import notify
    from web import api_v2_push
    api = api_factory(one())
    monkeypatch.setenv("RECON_PUSH_ENABLED", "1")
    caplog.set_level(logging.DEBUG)

    def boom(topic, message, title):
        raise ConnectionError("secret-detail")

    monkeypatch.setattr(notify, "default_send", boom)
    _pending(api, "missing")
    assert api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": ACTOR}).status_code == 200
    assert api_v2_push.wait_idle(5)
    assert "secret-detail" not in caplog.text and _status(api) == [("missing", "failed")]
    monkeypatch.setattr(notify, "default_send", lambda *a: None)
    api.post("/api/v2/assignments/1/stage", {"action": "undo", "actor": ACTOR})
    assert api_v2_push.wait_idle(5) and _status(api) == [("missing", "sent")]


def test_no_thread_when_push_is_off(api_factory, monkeypatch):
    from web import api_v2_push
    api = api_factory(one())
    started = []
    monkeypatch.setattr(api_v2_push.threading, "Thread", lambda *a, **k: started.append(1))
    api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": ACTOR})
    assert started == []


def test_kick_releases_the_lock_when_the_thread_cannot_start(monkeypatch, caplog):
    from web import api_v2_push

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("no threads")

    monkeypatch.setattr(api_v2_push.threading, "Thread", Broken)
    monkeypatch.setenv("RECON_PUSH_ENABLED", "1")
    api_v2_push.kick("/nonexistent.db", NOW)
    assert api_v2_push.wait_idle(1)


def test_sender_with_an_unopenable_database_logs_and_releases(monkeypatch, caplog, tmp_path):
    from web import api_v2_push
    monkeypatch.setenv("RECON_PUSH_ENABLED", "1")
    caplog.set_level(logging.DEBUG)
    api_v2_push.kick(str(tmp_path / "nope" / "x.db"), NOW)
    assert api_v2_push.wait_idle(5)
    assert "push" in caplog.text


# ---- S6 ----
@pytest.mark.parametrize("aid", [0, 2 ** 53 + 1, 2 ** 63 + 5, 10 ** 30])
def test_out_of_range_ids_are_404_not_500(api_factory, aid):
    api = api_factory(PAIR)
    err(api.get(f"/api/v2/assignments/{aid}"), 404, "not_found")
    for tail, body in (("stage", {"action": "done"}), ("merge", {"into": 2}), ("split", {"link_id": 1}),
                       ("teacher-confirmed", {"note": "abc"})):
        err(api.post(f"/api/v2/assignments/{aid}/{tail}", {**body, "actor": ACTOR}), 404, "not_found")


def test_framework_errors_get_the_envelope_and_nosniff(api_factory):
    api = api_factory(PAIR)
    r = api.get("/api/v2/nope")
    err(r, 404, "not_found")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    r = api.client.delete("/api/v2/summary")
    err(r, 405, "method_not_allowed")
    err(api.get("/api/v2/assignments/abc"), 404, "not_found")
    err(api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": ACTOR}, ctype="text/plain"), 415,
        "unsupported_media_type")
    err(api.post("/api/v2/assignments/1/stage", raw=b"x" * 9000), 413, "payload_too_large")
    for resp in (api.get("/api/v2/summary"), api.get("/api/v2/nope"), api.post("/api/v2/assignments/1/stage", {},
                                                                              token=None)):
        assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_non_v2_framework_errors_are_untouched(api_factory):
    api = api_factory(PAIR)
    r = api.get("/api/definitely-not-a-route")
    body = r.get_json(silent=True)
    assert r.status_code == 404 and not (isinstance(body, dict) and "meta" in body)


def test_framework_500_inside_v2_gets_the_envelope(api_factory, monkeypatch):
    from school_dashboard.assignments import service
    api = api_factory(one())

    def boom(*a, **k):
        raise RuntimeError("secret-detail")

    monkeypatch.setattr(service, "tap", boom)
    r = api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": ACTOR})
    err(r, 500, "internal")
    assert b"secret-detail" not in r.data


def test_validation_trigger_rejection_is_409_rejected_and_logs_the_type_only(api_factory, monkeypatch, caplog):
    from school_dashboard.assignments import service
    api = api_factory(one())
    caplog.set_level(logging.DEBUG)

    def boom(*a, **k):
        raise sqlite3.IntegrityError("assignments.title secret-detail")

    monkeypatch.setattr(service, "tap", boom)
    err(api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": ACTOR}), 409, "rejected")
    assert "IntegrityError" in caplog.text and "secret-detail" not in caplog.text


@pytest.mark.parametrize("email", ["nope", "a@b", "a b@c.de", "@x.org", "x@@y.org", "a@b.c" + "d" * 200])
def test_invalid_teacher_email_is_400(api_factory, email):
    api = api_factory(PAIR)
    r = api.put("/api/v2/classes", {"kid": "Alex", "class_name": "Math 6", "teacher_email": email, "actor": ACTOR})
    err(r, 400, "invalid_field")


def test_aliases_are_stored_unescaped_and_their_total_size_is_bounded(api_factory):
    api = api_factory(PAIR)
    body = {"kid": "Alex", "class_name": "Math 6", "actor": ACTOR}
    def put(extra):
        return api.put("/api/v2/classes", raw=json.dumps({**body, **extra}, ensure_ascii=False).encode())

    assert put({"aliases": ["Álgebra é", "Maths"]}).status_code == 200
    raw = api.q("SELECT aliases FROM class_settings WHERE kid = 'Alex'")[0]["aliases"]
    assert "Álgebra é" in raw and "\\u" not in raw
    big = [f"{i:02d}" + "é" * 98 for i in range(20)]
    err(put({"aliases": big}), 400, "invalid_field")
    ok = [f"{i:02d}" + "é" * 58 for i in range(20)]
    assert put({"aliases": ok}).status_code == 200


# ---- C7 / C5 ----
def test_list_meta_has_total_before_pagination(api_factory):
    api = api_factory(PAIR)
    body = data(api.get("/api/v2/assignments?limit=1"))
    assert body["meta"]["count"] == 1 and body["meta"]["total"] == 2
    body = data(api.get("/api/v2/assignments?limit=5&offset=1"))
    assert body["meta"]["count"] == 1 and body["meta"]["total"] == 2
    assert data(api.get("/api/v2/assignments?kid=Sam"))["meta"]["total"] == 0


def _kid(api, name):
    return next(k for k in data(api.get("/api/v2/summary"))["data"]["kids"] if k["name"] == name)


def test_all_clear_stays_true_when_everything_is_verified(api_factory):
    sam = _kid(api_factory([sgy("Essay", "Math 6", "s1", due="2026-12-30")]), "Sam")
    assert sam["all_clear"] is True and sam["all_clear_reason"] is None


def test_all_clear_false_with_stale_ingest(tmp_path, monkeypatch):
    from recon.api_support import make_api
    api = make_api(tmp_path, monkeypatch, [sgy("Essay", "Math 6", "s1", due="2026-12-30")], meta="stale")
    sam = _kid(api, "Sam")
    assert sam["all_clear"] is False and sam["all_clear_reason"] == "unverified"


def test_all_clear_false_when_one_of_the_kids_rows_is_unverified(api_factory):
    api = api_factory([sgy("Essay", "Math 6", "s1", due="2026-12-30")])
    meta = json.loads((api.state / "sync_meta.json").read_text())
    old = "2026-11-10T10:00:00-05:00"
    meta["sgy"] = {"last_run": old, "last_ok": old, "last_result": "ok"}
    (api.state / "sync_meta.json").write_text(json.dumps(meta))
    alex = _kid(api, "Alex")
    assert alex["counts"]["needs_action"] == 0 and alex["counts"]["do_tonight"] == 0
    assert alex["all_clear"] is False and alex["all_clear_reason"] == "unverified"
    assert _kid(api, "Sam")["all_clear"] is True


def test_all_clear_reason_is_null_when_there_is_real_work(api_factory):
    alex = _kid(api_factory(one()), "Alex")
    assert alex["all_clear"] is False and alex["all_clear_reason"] is None


# ---- C2 ----
def test_class_put_is_a_partial_update(api_factory):
    api = api_factory(PAIR)
    body = {"kid": "Alex", "class_name": "Math 6", "actor": ACTOR}
    full = {"grace_school_days": 3, "paper_only": True, "aliases": ["Algebra"], "teacher_name": "Ms Lee",
            "teacher_email": "lee@example.org"}
    assert api.put("/api/v2/classes", {**body, **full}).status_code == 200
    r = api.put("/api/v2/classes", {**body, "teacher_name": None})
    assert r.status_code == 200
    row = api.q("SELECT * FROM class_settings WHERE kid = 'Alex'")[0]
    assert (row["grace_school_days"], row["paper_only"], row["teacher_name"], row["teacher_email"]) == (
        3, 1, None, "lee@example.org")
    assert json.loads(row["aliases"]) == ["Algebra"]
    api.put("/api/v2/classes", {**body, "grace_school_days": None, "paper_only": False, "aliases": []})
    row = api.q("SELECT * FROM class_settings WHERE kid = 'Alex'")[0]
    assert (row["grace_school_days"], row["paper_only"], row["aliases"], row["teacher_email"]) == (
        None, 0, None, "lee@example.org")


def test_class_put_first_write_uses_defaults_for_omitted_fields(api_factory):
    api = api_factory(PAIR)
    api.put("/api/v2/classes", {"kid": "Alex", "class_name": "Math 6", "grace_school_days": 4, "actor": ACTOR})
    row = api.q("SELECT * FROM class_settings WHERE kid = 'Alex'")[0]
    assert (row["grace_school_days"], row["paper_only"], row["aliases"]) == (4, 0, None)


# ---- S3 over the API ----
def test_classes_get_returns_the_last_20_audit_events_per_kid(api_factory):
    api = api_factory(PAIR)
    body = {"kid": "Alex", "class_name": "Math 6", "actor": ACTOR}
    for g in range(11):
        api.put("/api/v2/classes", {**body, "grace_school_days": g})
    for g in range(10, -1, -1):
        api.put("/api/v2/classes", {**body, "grace_school_days": g})
    out = data(api.get("/api/v2/classes?kid=Alex"))
    evs = out["meta"]["events"]
    assert len(evs) == 20 and evs[0]["changes"] == {"grace_school_days": [1, 0]}
    assert evs[0]["actor"] == "Dana" and evs[0]["setter"] == "parent" and evs[0]["class_key"] == "math 6"
    assert data(api.get("/api/v2/classes?kid=Sam"))["meta"]["events"] == []


# ---- remerge result ----
def test_remerge_returns_merged_and_ambiguous_counts(api_factory):
    api = api_factory([sgy("Essay", "Algebra", "s1"), ps("Essay", "Math 6", "p1")],
                      setup={"class_settings": [{"kid": "Alex", "class_key": "math 6", "aliases": ["Algebra"]}]})
    out = data(api.post("/api/v2/kids/Alex/remerge", {"actor": ACTOR}))["data"]
    assert out["merged"] == [[2, 1]] and out["ambiguous_groups"] == 0


def test_config_advertises_write_token_header_and_min_length(api_factory):
    cfg = data(api_factory(PAIR).get("/api/v2/config"))["data"]
    assert cfg["write_token_header"] == "X-Recon-Token" and cfg["write_token_min_length"] == 32
    assert TOKEN not in json.dumps(cfg)
