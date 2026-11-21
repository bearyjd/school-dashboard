"""Review round 2: note link scrubbing, one open draft per assignment, service-layer parent-only checks. SYNTHETIC."""
from datetime import datetime

import pytest

from recon.api_support import ACTOR, KID_ACTOR, data, make_api  # noqa: F401
from recon.s4_support import NOW
from recon.test_api_v2_notes import BASE, note, seed, teacher

LINKS = ["evil.example/login", "xn--80ak6aa92e.com/pay", "ftp://x", "hxxp://x", "javascript:alert(1)",
         "evil。example／x", "Wᴡw.evil.example", "evil.example:8080", "a.io?x=1", "foo.bar#frag", "mailto:a",
         "HTTP://X.TEST", "sub.evil.example/a/b"]
KEEP = ["Essay Draft", "Nov 16", "3.5", "Ms. Rivera", "p. 12", "Mr. Smith", "Chapter 4 notes", "5.0 pts", "St. Louis"]


def make(title):
    from school_dashboard.assignments import notes
    item = notes.NoteItem(title=title, class_name=None, marked_on=None)
    return notes.draft(item, flag="missing", parent_name="Dana", kid="Alex", teacher_name=None,
                       today=datetime(2026, 11, 18).date())


@pytest.mark.parametrize("link", LINKS)
def test_link_like_text_is_removed(link):
    d = make(f"Read {link} now")
    for text in (d.subject, d.body):
        folded = text.lower()
        assert "evil" not in folded and "xn--" not in folded and "ftp" not in folded and "hxxp" not in folded
        assert "javascript" not in folded and "://" not in text and "x.test" not in folded
        assert "a.io" not in folded and "foo.bar" not in folded and "mailto" not in folded
    assert "Read" in d.subject and "now" in d.subject


@pytest.mark.parametrize("word", KEEP)
def test_ordinary_text_survives(word):
    d = make(f"Read {word} now")
    assert f"Read {word} now" in d.subject


def seeded(api_factory):
    api = api_factory(seed("missing"))
    teacher(api)
    return api


def test_post_twice_returns_same_open_draft(api_factory):
    api = seeded(api_factory)
    first = note(api)
    second = note(api)
    assert first.status_code == 201 and second.status_code == 200
    a, b = data(first), data(second)
    assert a["data"] == b["data"] and b["ok"] and b["error"] is None
    assert len(api.q("SELECT id FROM note_drafts")) == 1


def test_new_draft_after_discard(api_factory):
    api = seeded(api_factory)
    nid = data(note(api))["data"]["id"]
    api.send("patch", f"{BASE}/1/note/{nid}", {"status": "discarded", "actor": ACTOR})
    r = note(api)
    assert r.status_code == 201 and data(r)["data"]["id"] != nid


def test_other_flag_gets_new_draft(api_factory):
    api = seeded(api_factory)
    first = data(note(api))["data"]
    api.exec("UPDATE note_drafts SET flag = 'disputed'")
    r = note(api)
    assert r.status_code == 201 and data(r)["data"]["id"] != first["id"]


def test_notes_for_lists_at_most_20_newest_first(api_factory):
    api = seeded(api_factory)
    for _ in range(25):
        api.exec("INSERT INTO note_drafts (assignment_id, flag, body, status, created_at, actor)"
                 " VALUES (1, 'missing', 'b', 'discarded', '2026-11-18T00:00:00', 'Dana')")
    got = data(api.get(f"{BASE}/1"))["data"]["notes"]
    assert len(got) == 20 and [n["id"] for n in got] == sorted((n["id"] for n in got), reverse=True)
    assert got[0]["id"] == 25


def _service(api):
    from school_dashboard.assignments import service
    from web import api_v2_context as ctxmod
    conn = api.conn()
    return service, conn, ctxmod.load(conn, NOW).view()


def test_service_create_note_is_parent_only(api_factory):
    from school_dashboard.assignments.service_types import Actor, Conflict
    api = seeded(api_factory)
    service, conn, view = _service(api)
    with pytest.raises(Conflict) as exc:
        service.create_note(conn, 1, Actor("kid", "Alex"), view=view)
    assert exc.value.args[0] == "forbidden_role"
    assert api.q("SELECT id FROM note_drafts") == []


def test_service_set_note_status_is_parent_only(api_factory):
    from school_dashboard.assignments.service_types import Actor, Conflict
    api = seeded(api_factory)
    nid = data(note(api))["data"]["id"]
    service, conn, _ = _service(api)
    with pytest.raises(Conflict) as exc:
        service.set_note_status(conn, 1, nid, "discarded", Actor("kid", "Alex"))
    assert exc.value.args[0] == "forbidden_role"
    assert api.q("SELECT status FROM note_drafts")[0]["status"] == "draft"
