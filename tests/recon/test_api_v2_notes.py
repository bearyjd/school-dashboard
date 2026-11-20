"""Teacher-note draft routes (S8): drafts only, the app never sends anything. SYNTHETIC data."""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from recon.api_support import ACTOR, KID_ACTOR, data, err, make_api, sgy
from recon.s4_support import NOW

ROOT = Path(__file__).resolve().parents[2]
BASE = "/api/v2/assignments"
PORTAL = "https://portal.school.test/assignment/77"
EMAIL = "t.rivera@school.test"


def seed(flag, *, url=PORTAL, kid="Alex", title="Essay Draft"):
    """One record (id 1) whose flag on NOW (Wed 2026-11-18 17:00) is `flag`."""
    fields = {"disputed": dict(stage=3, portal_status="missing", stage_since="2026-11-16T12:00:00-05:00",
                               due_date="2026-11-17"),
              "done_not_received": dict(stage=2, stage_since="2026-11-12T12:00:00-05:00", due_date="2026-11-13"),
              "missing": dict(stage=1, due_date="2026-11-16"),
              "do_tonight": dict(stage=1, due_date="2026-11-19"),
              "waiting_on_teacher": dict(stage=2, stage_since="2026-11-18T09:00:00-05:00", due_date="2026-11-19"),
              "upcoming": dict(stage=1, due_date="2026-12-20")}[flag]
    link = {"source": "schoology", "external_key": "s1", "norm_status": "open", "url": url}
    return [{"kid": kid, "title": title, "title_key": title.casefold(), "class_name": "Math 6",
             "class_key": "math 6", **fields, "links": [link]}]


def teacher(api, name="Ms. Rivera", email: str | None = EMAIL):
    api.exec("INSERT INTO class_settings (kid, class_key, teacher_name, teacher_email) VALUES ('Alex', 'math 6', ?, ?)",
             (name, email))


def note(api, aid=1, actor=ACTOR, **kw):
    return api.post(f"{BASE}/{aid}/note", {"actor": actor}, **kw)


def query(url):
    s = urlsplit(url)
    return unquote(s.path), parse_qs(s.query, strict_parsing=True)


@pytest.mark.parametrize("flag", ["disputed", "done_not_received", "missing"])
def test_post_creates_draft(api_factory, flag):
    api = api_factory(seed(flag))
    teacher(api)
    r = note(api)
    body = data(r)
    assert r.status_code == 201 and body["ok"] and body["error"] is None
    d = body["data"]
    assert set(d) == {"id", "flag", "subject", "body", "teacher_email", "mailto", "portal_url"}
    assert d["flag"] == flag and d["subject"] == "Alex - Essay Draft (Math 6)"
    assert d["body"].startswith("Hello Ms. Rivera,\n") and d["body"].endswith("Thank you,\nDana")
    assert "http" not in d["body"] and "@" not in d["body"] and "!" not in d["body"]
    assert d["teacher_email"] == EMAIL
    to, q = query(d["mailto"])
    assert to == EMAIL and set(q) == {"subject", "body"} and q["subject"] == [d["subject"]]
    assert q["body"] == [d["body"].replace("\n", "\r\n")]
    assert d["portal_url"] == (PORTAL if flag == "disputed" else None)
    row = api.q("SELECT assignment_id, flag, body, status, actor, created_at FROM note_drafts")[0]
    assert (row["assignment_id"], row["flag"], row["body"], row["status"], row["actor"]) == (1, flag, d["body"],
                                                                                           "draft", "Dana")
    assert row["created_at"] == NOW.isoformat() and d["id"] == 1


def test_without_teacher_settings(api_factory):
    api = api_factory(seed("missing"))
    d = data(note(api))["data"]
    assert d["body"].startswith("Hello,\n") and d["teacher_email"] is None and d["mailto"] is None


def test_teacher_name_without_email(api_factory):
    api = api_factory(seed("missing"))
    teacher(api, email=None)
    d = data(note(api))["data"]
    assert d["body"].startswith("Hello Ms. Rivera,\n") and d["mailto"] is None


@pytest.mark.parametrize("email", ["not-an-address", "a@b.org?cc=x@y.org", "a,b@c.org", "a b@c.org"])
def test_stored_bad_address_gives_no_mailto(api_factory, email):
    api = api_factory(seed("missing"))
    teacher(api, email=email)
    d = data(note(api))["data"]
    assert d["mailto"] is None and d["teacher_email"] is None


def test_long_title_is_capped_and_mailto_still_built(api_factory):
    api = api_factory(seed("missing", title="t" * 300))
    teacher(api)
    assert data(note(api))["data"]["mailto"] is not None  # title is capped at 120: stays under 1800


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://u:p@portal.school.test/x",
                                 "https://portal.school.test/x?token=abc", None])
def test_unsafe_portal_url_is_dropped(api_factory, url):
    api = api_factory(seed("disputed", url=url))
    assert data(note(api))["data"]["portal_url"] is None


def test_portal_url_skips_dead_links_and_takes_first_live_safe_one(api_factory):
    s = seed("disputed")
    s[0]["links"] = [{"source": "powerschool", "external_key": "p1", "norm_status": "open", "live": False,
                      "url": "https://dead.school.test/1"},
                     {"source": "schoology", "external_key": "s1", "norm_status": "open", "url": PORTAL}]
    api = api_factory(s)
    assert data(note(api))["data"]["portal_url"] == PORTAL


@pytest.mark.parametrize("flag", ["do_tonight", "waiting_on_teacher", "upcoming"])
def test_other_flags_are_409(api_factory, flag):
    api = api_factory(seed(flag))
    err(note(api), 409, "no_note_for_flag")
    assert api.q("SELECT COUNT(*) AS n FROM note_drafts")[0]["n"] == 0


def test_closed_record_is_409_and_unknown_is_404(api_factory):
    api = api_factory(seed("missing"))
    api.exec("UPDATE assignments SET stage = 4 WHERE id = 1")
    err(note(api), 409, "no_note_for_flag")
    err(note(api, aid=99), 404, "not_found")
    err(note(api, aid=0), 404, "not_found")


def test_flag_is_computed_at_request_time(api_factory):
    from datetime import timedelta
    api = api_factory(seed("do_tonight"))
    err(note(api), 409, "no_note_for_flag")
    api.clock["now"] = NOW + timedelta(days=3)
    assert data(note(api))["data"]["flag"] == "missing"


def test_kid_role_is_403_and_stores_nothing(api_factory):
    api = api_factory(seed("missing"))
    err(note(api, actor=KID_ACTOR), 403, "forbidden_role")
    assert api.q("SELECT COUNT(*) AS n FROM note_drafts")[0]["n"] == 0


def test_hostile_title_and_parent_cannot_inject(api_factory):
    api = api_factory(seed("missing", title="Essay\r\ncc: x@y.org http://evil.test/a urgent! %0Acc: z"))
    teacher(api)
    d = data(note(api, actor={"role": "parent", "name": "Dana\r\nBcc: q@r.org"}))["data"]
    to, q = query(d["mailto"])
    assert to == EMAIL and set(q) == {"subject", "body"}
    assert all(x not in d["body"].lower() for x in ("http", "@", "!", "urgent")) and "\r" not in d["body"]
    assert "\r" not in d["mailto"] and "\n" not in d["mailto"]


def seeded_note(api):
    d = data(note(api))["data"]
    return d["id"]


@pytest.mark.parametrize("status", ["sent_marked", "discarded"])
def test_patch_marks_status_and_never_emails(api_factory, status):
    api = api_factory(seed("missing"))
    nid = seeded_note(api)
    r = api.send("patch", f"{BASE}/1/note/{nid}", {"status": status, "actor": ACTOR})
    d = data(r)
    assert r.status_code == 200 and d["ok"] and d["data"]["note"] == {
        "id": nid, "flag": "missing", "status": status, "created_at": NOW.isoformat(), "actor": "Dana"}
    assert api.q("SELECT status FROM note_drafts WHERE id = ?", (nid,))[0]["status"] == status
    err(api.send("patch", f"{BASE}/1/note/{nid}", {"status": "discarded", "actor": ACTOR}), 409,
        "invalid_transition")


@pytest.mark.parametrize("status", ["draft", "sent", "SENT_MARKED", "", None, 1, True, ["discarded"]])
def test_patch_bad_status_is_400(api_factory, status):
    api = api_factory(seed("missing"))
    nid = seeded_note(api)
    err(api.send("patch", f"{BASE}/1/note/{nid}", {"status": status, "actor": ACTOR}), 400, "invalid_field")
    assert api.q("SELECT status FROM note_drafts")[0]["status"] == "draft"


def test_patch_other_assignments_note_or_unknown_is_404_and_kid_403(api_factory):
    api = api_factory([*seed("missing"), {**sgy("Quiz", "Math 6", "s2", due="2026-11-16")}])
    nid = seeded_note(api)
    body = {"status": "discarded", "actor": ACTOR}
    err(api.send("patch", f"{BASE}/2/note/{nid}", body), 404, "not_found")
    err(api.send("patch", f"{BASE}/1/note/999", body), 404, "not_found")
    err(api.send("patch", f"{BASE}/1/note/0", body), 404, "not_found")
    err(api.send("patch", f"{BASE}/1/note/{nid}", {**body, "actor": KID_ACTOR}), 403, "forbidden_role")
    assert api.q("SELECT status FROM note_drafts")[0]["status"] == "draft"


def test_detail_lists_notes_without_body(api_factory):
    api = api_factory(seed("missing"))
    assert data(api.get(f"{BASE}/1"))["data"]["notes"] == []
    first = seeded_note(api)
    second = seeded_note(api)
    api.send("patch", f"{BASE}/1/note/{first}", {"status": "discarded", "actor": ACTOR})
    notes = data(api.get(f"{BASE}/1"))["data"]["notes"]
    assert [n["id"] for n in notes] == [second, first]
    assert set(notes[0]) == {"id", "flag", "status", "created_at", "actor"}
    assert notes[0]["status"] == "draft" and notes[1]["status"] == "discarded"
    assert "body" not in json.dumps(notes) and "notes" not in data(api.get(BASE))["data"][0]


def test_nothing_is_sent(api_factory, monkeypatch):
    import requests
    from web import api_v2_push
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(api_v2_push, "kick", lambda *a, **k: calls.append(a))
    api = api_factory(seed("disputed"))
    teacher(api)
    before = api.q("SELECT COUNT(*) AS n FROM push_outbox")[0]["n"]
    events = api.q("SELECT COUNT(*) AS n FROM assignment_events")[0]["n"]
    nid = seeded_note(api)
    api.send("patch", f"{BASE}/1/note/{nid}", {"status": "sent_marked", "actor": ACTOR})
    assert calls == [] and api.q("SELECT COUNT(*) AS n FROM push_outbox")[0]["n"] == before
    assert api.q("SELECT COUNT(*) AS n FROM assignment_events")[0]["n"] == events


def test_no_module_can_send_mail():
    files = [*(ROOT / "school_dashboard" / "assignments").rglob("*.py"), *(ROOT / "web").glob("api_v2*.py")]
    assert len(files) > 20
    bad = re.compile(r"smtplib|email\.mime|from email import|sendmail|gmail\s+send|\bgog\b.*\bsend\b", re.I)
    hits = [f.name for f in files if any(bad.search(ln) for ln in f.read_text().splitlines())]
    assert hits == []


NOTE = ("post", f"{BASE}/1/note")
PATCH = ("patch", f"{BASE}/1/note/1")
VALID = {NOTE: {"actor": ACTOR}, PATCH: {"status": "discarded", "actor": ACTOR}}
ROUTES = list(VALID)
IDS = [f"{m}:{p}" for m, p in ROUTES]
SEED = seed("missing")


def call(api, route, body=None, **kw):
    return api.send(route[0], route[1], VALID[route] if body is None else body, **kw)


def count(api):
    return api.q("SELECT COUNT(*) AS n FROM note_drafts")[0]["n"]


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_maintenance_blocks(api_factory, route):
    from school_dashboard import maintenance
    api = api_factory(SEED)
    maintenance.enable("test")
    r = call(api, route)
    assert r.status_code == 503 and r.get_json() == {"error": "maintenance"} and count(api) == 0


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_disabled_flag_and_not_migrated(tmp_path, monkeypatch, route):
    api = make_api(tmp_path, monkeypatch, SEED, enabled=False)
    err(call(api, route), 404, "recon_disabled")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_not_migrated_is_409(tmp_path, monkeypatch, route):
    err(call(make_api(tmp_path, monkeypatch, migrated=False), route), 409, "not_migrated")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
@pytest.mark.parametrize("token", [None, "wrong-token-wrong-token-wrong-token-0"])
def test_token_is_required(api_factory, route, token):
    api = api_factory(SEED)
    err(call(api, route, token=token), 401, "bad_token")
    assert count(api) == 0


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded", None])
def test_wrong_content_type_is_415(api_factory, route, ctype):
    err(call(api_factory(SEED), route, ctype=ctype), 415, "unsupported_media_type")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_oversized_and_non_object(api_factory, route):
    api = api_factory(SEED)
    err(call(api, route, {**VALID[route], "pad": "x" * 9000}), 413, "payload_too_large")
    for raw in ("[]", "null", "nope", ""):
        assert call(api, route, raw=raw).status_code == 400


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_unknown_keys_and_bad_actor(api_factory, route):
    api = api_factory(SEED)
    err(call(api, route, {**VALID[route], "zzz": 1}), 400, "unknown_field")
    err(call(api, route, {**VALID[route], "actor": {**ACTOR, "x": 1}}), 400, "unknown_field")
    for actor in (None, "Dana", {"role": "admin", "name": "D"}, {"role": "parent", "name": ""}):
        body = {k: v for k, v in VALID[route].items() if k != "actor"}
        if actor is not None:
            body["actor"] = actor
        err(call(api, route, body), 400, "invalid_actor")


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_busy_is_503(api_factory, monkeypatch, route):
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
