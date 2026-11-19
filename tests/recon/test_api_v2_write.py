"""POST/PUT routes of /api/v2: happy paths, validation classes, domain rules."""
import pytest

from recon.api_support import ACTOR, KID_ACTOR, data, err, one, ps, sgy
from recon.s4_support import link_set

BASE = "/api/v2/assignments"


def stage(api, aid=1, action="done", actor=ACTOR):
    return api.post(f"{BASE}/{aid}/stage", {"action": action, "actor": actor})


def test_stage_tap_returns_item_and_fresh_counts(api_factory):
    api = api_factory(one())
    before = data(api.get("/api/v2/summary"))["data"]["kids"][0]["counts"]
    r = stage(api)
    body = data(r)
    assert r.status_code == 200 and body["ok"] and body["error"] is None
    item, counts = body["data"]["item"], body["data"]["counts"]
    assert item["id"] == 1 and item["stage"] == 2 and item["stage_label"] == "Done at home"
    assert before["do_tonight"] == 1 and counts["do_tonight"] == 0 and counts["waiting_on_teacher"] == 1
    assert "links" in item and "events" not in item


def test_stage_events_are_attributed(api_factory):
    api = api_factory(one())
    stage(api, actor={"role": "kid", "name": "Alex"})
    rows = api.q("SELECT setter, actor, kind FROM assignment_events WHERE assignment_id = 1 AND kind = 'stage'")
    assert [(r["setter"], r["actor"], r["kind"]) for r in rows] == [("kid", "Alex", "stage")]


def test_kid_may_tap_stage(api_factory):
    api = api_factory(one())
    assert stage(api, actor=KID_ACTOR).status_code == 200
    assert stage(api, action="undo", actor=KID_ACTOR).status_code == 200


@pytest.mark.parametrize("path,body", [
    ("/api/v2/assignments/1/teacher-confirmed", {"note": "Teacher confirmed it"}),
    ("/api/v2/assignments/1/merge", {"into": 2}),
    ("/api/v2/assignments/1/split", {"link_id": 1}),
    ("/api/v2/kids/Alex/remerge", {})])
def test_kid_role_limited_to_stage(api_factory, path, body):
    api = api_factory([sgy("Essay", "Math 6", "s1", stage=2), ps("Essay", "Math 6", "p1")])
    err(api.post(path, {**body, "actor": KID_ACTOR}), 403, "forbidden_role")
    err(api.put("/api/v2/classes", {"kid": "Alex", "class_name": "Math 6", "actor": KID_ACTOR}), 403,
        "forbidden_role")
    assert api.q("SELECT COUNT(*) AS n FROM assignment_events")[0]["n"] == 0


def test_no_stage_sequence_reaches_stage_4(api_factory):
    api = api_factory(one())
    for role in ("parent", "kid"):
        for _ in range(2):
            for action in ("done", "turned_in", "turned_in", "undo", "done", "turned_in", "undo", "undo"):
                stage(api, action=action, actor={"role": role, "name": "Pat"})
                item = data(api.get(f"{BASE}/1"))["data"]
                assert item["stage"] < 4 and item["received_evidence"] is None
    assert api.q("SELECT stage FROM assignments WHERE id = 1")[0]["stage"] < 4


def test_teacher_confirmed_by_parent_with_note_reaches_stage_4(api_factory):
    api = api_factory([sgy("Essay", "Math 6", "s1", stage=2)])
    r = api.post(f"{BASE}/1/teacher-confirmed", {"note": "Mrs R said it arrived", "actor": ACTOR})
    item = data(r)["data"]["item"]
    assert r.status_code == 200 and item["stage"] == 4 and item["received_evidence"] == "teacher_confirmed"
    ev = data(api.get(f"{BASE}/1"))["data"]["events"][0]
    assert ev["detail"] == "Mrs R said it arrived" and ev["setter"] == "parent" and ev["actor"] == "Dana"


def test_teacher_confirmed_from_stage_1_is_conflict(api_factory):
    api = api_factory(one())
    err(api.post(f"{BASE}/1/teacher-confirmed", {"note": "Mrs R said so", "actor": ACTOR}), 409,
        "invalid_transition")


def test_task_done_closes_and_undo_reopens(api_factory):
    seed = [{"kid": "Alex", "title": "Chore", "title_key": "chore", "class_name": "", "class_key": "",
             "due_date": "2026-11-19", "kind": "task", "links": []}]
    api = api_factory(seed)
    assert data(stage(api))["data"]["item"]["stage"] == 2
    assert api.q("SELECT closed_at FROM assignments WHERE id = 1")[0]["closed_at"] is not None
    assert data(api.get(f"{BASE}?kind=task"))["data"] == []
    assert len(data(api.get(f"{BASE}?kind=task&include_closed=1"))["data"]) == 1
    assert data(stage(api, action="undo"))["data"]["item"]["stage"] == 1
    assert api.q("SELECT closed_at FROM assignments WHERE id = 1")[0]["closed_at"] is None
    assert len(data(api.get(f"{BASE}?kind=task"))["data"]) == 1
    err(stage(api, action="turned_in"), 409, "invalid_transition")


def test_practice_tap_is_conflict(api_factory):
    seed = [{"kid": "Alex", "title": "IXL Math", "title_key": "ixl math", "class_name": "Math", "class_key": "math",
             "due_date": None, "kind": "practice",
             "links": [{"source": "ixl", "external_key": "ixl1", "norm_status": "open"}]}]
    api = api_factory(seed)
    for action in ("done", "turned_in", "undo"):
        err(stage(api, action=action), 409, "invalid_transition")


def test_unknown_assignment_and_merged_away(api_factory):
    api = api_factory([sgy("Essay", "Math 6", "s1"), ps("Essay", "Math 6", "p1")])
    err(stage(api, 99), 404, "not_found")
    api.post(f"{BASE}/2/merge", {"into": 1, "actor": ACTOR})
    err(stage(api, 2), 409, "merged_away")


def test_merge_then_split_restores_link_sets(api_factory):
    api = api_factory([sgy("Essay", "Math 6", "s1"), ps("Essay", "Math 6", "p1")])
    c = api.conn()
    before = (link_set(c, 1), link_set(c, 2))
    c.close()
    r = api.post(f"{BASE}/2/merge", {"into": 1, "actor": ACTOR})
    item = data(r)["data"]["item"]
    assert r.status_code == 200 and item["id"] == 1 and {ln["source"] for ln in item["links"]} == {
        "schoology", "powerschool"}
    ps_link = next(ln["id"] for ln in item["links"] if ln["source"] == "powerschool")
    r2 = api.post(f"{BASE}/1/split", {"link_id": ps_link, "actor": ACTOR})
    new = data(r2)["data"]["item"]
    assert r2.status_code == 200 and new["id"] not in (1, 2) and [ln["source"] for ln in new["links"]] == [
        "powerschool"]
    c = api.conn()
    assert link_set(c, 1) == before[0] and link_set(c, new["id"]) == before[1]
    c.close()


def test_merge_conflicts(api_factory):
    seed = [sgy("Essay", "Math 6", "s1"), {**sgy("Essay", "Math 6", "p1"), "kid": "Sam"}]
    api = api_factory(seed)
    err(api.post(f"{BASE}/1/merge", {"into": 1, "actor": ACTOR}), 409, "same_record")
    err(api.post(f"{BASE}/1/merge", {"into": 2, "actor": ACTOR}), 409, "different_kid")
    err(api.post(f"{BASE}/1/merge", {"into": 77, "actor": ACTOR}), 404, "not_found")
    err(api.post(f"{BASE}/1/split", {"link_id": 1, "actor": ACTOR}), 409, "single_link")


def test_split_unknown_link_404(api_factory):
    api = api_factory([sgy("Essay", "Math 6", "s1"), ps("Essay", "Math 6", "p1")])
    api.post(f"{BASE}/2/merge", {"into": 1, "actor": ACTOR})
    err(api.post(f"{BASE}/1/split", {"link_id": 999, "actor": ACTOR}), 404, "not_found")


def test_class_put_then_remerge_after_alias(api_factory):
    api = api_factory([sgy("Essay", "Language Arts", "s1"), ps("Essay", "ELA", "p1")])
    assert len(data(api.get(BASE))["data"]) == 2
    r = api.put("/api/v2/classes", {"kid": "Alex", "class_name": "Language Arts", "aliases": ["ELA"],
                                    "grace_school_days": 3, "paper_only": False, "teacher_name": "Ms R",
                                    "teacher_email": "r@school.invalid", "actor": ACTOR})
    body = data(r)
    assert r.status_code == 200 and body["data"]["counts"] is not None
    items = data(api.get(BASE))["data"]
    assert len(items) == 1 and len(items[0]["links"]) == 2
    rows = data(api.get("/api/v2/classes?kid=Alex"))["data"]
    assert rows[0]["grace_school_days"] == 3 and rows[0]["aliases"] == ["ELA"]


def test_remerge_route(api_factory):
    api = api_factory([sgy("Essay", "Language Arts", "s1"), ps("Essay", "ELA", "p1")])
    api.exec("INSERT INTO class_settings (kid, class_key, aliases) VALUES ('Alex', 'language arts', '[\"ELA\"]')")
    r = api.post("/api/v2/kids/Alex/remerge", {"actor": ACTOR})
    body = data(r)
    assert r.status_code == 200 and len(body["data"]["merged"]) == 1 and "counts" in body["data"]
    again = data(api.post("/api/v2/kids/Alex/remerge", {"actor": ACTOR}))
    assert again["data"]["merged"] == []
    err(api.post("/api/v2/kids/Zed/remerge", {"actor": ACTOR}), 400, "invalid_kid")


def test_class_put_unknown_kid_has_no_assignments(api_factory):
    api = api_factory(one())
    err(api.put("/api/v2/classes", {"kid": "Sam", "class_name": "Art", "actor": ACTOR}), 404, "not_found")
    err(api.put("/api/v2/classes", {"kid": "Alex", "class_name": "Art", "aliases": ["Art"], "actor": ACTOR}), 409,
        "invalid_class_settings")


def test_push_is_called_best_effort_and_never_breaks_the_response(api_factory, monkeypatch):
    from school_dashboard.assignments import notify
    calls = []

    def boom(conn, *, now, send=None):
        calls.append(now)
        raise RuntimeError("ntfy down")
    monkeypatch.setattr(notify, "send_pending", boom)
    api = api_factory(one())
    assert stage(api).status_code == 200 and len(calls) == 1
    api.get(f"{BASE}/1")
    assert len(calls) == 1  # reads never push
    err(stage(api, 99), 404, "not_found")
    assert len(calls) == 1  # failed writes never push


def test_failed_validation_changes_nothing(api_factory):
    api = api_factory(one())
    for body in ({"action": "bogus", "actor": ACTOR}, {"action": "done"}, {"action": "done", "actor": ACTOR, "x": 1}):
        assert api.post(f"{BASE}/1/stage", body).status_code == 400
    assert api.q("SELECT COUNT(*) AS n FROM assignment_events")[0]["n"] == 0
    assert api.q("SELECT stage FROM assignments")[0]["stage"] == 1
