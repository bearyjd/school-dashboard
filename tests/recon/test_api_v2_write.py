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
    ev = next(e for e in data(api.get(f"{BASE}/1"))["data"]["events"] if e["kind"] == "stage")
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


def test_dismiss_survives_ingest_and_guarded_undo_preserves_stage(api_factory):
    from school_dashboard.assignments import digest_run, ingest, readctx
    from recon.ingest_support import build_results

    api = api_factory()
    now = api.clock["now"]
    scrape = {"sgy": {"Alex": [{"title": "Essay", "course": "Math 6", "status": "open",
                                "link": "https://sgy.example.invalid/a/1"}]}, "hash": {"schoology": "s1"}}
    results, hashes = build_results(scrape)
    assert ingest.run(api.db, results, file_hashes=hashes, now=now, no_school_rows=[]).ok
    assert stage(api, action="turned_in").status_code == 200
    before = api.q("SELECT stage_since FROM assignments")[0][0]
    dismissed = data(stage(api, action="dismiss", actor=KID_ACTOR))["data"]
    stamp = dismissed["item"]["dismissed_at"]
    assert stamp == dismissed["dismissed_at"] == now.isoformat()
    assert dismissed["item"]["stage"] == 3 and dismissed["item"]["flag"] is None
    assert dismissed["counts"]["waiting_on_teacher"] == 0
    err(stage(api, action="dismiss"), 409, "invalid_transition")
    err(stage(api, action="done"), 409, "invalid_transition")
    err(stage(api, action="undo"), 409, "invalid_transition")

    scrape["sgy"]["Alex"][0]["title"] = "Revised Essay"
    results, hashes = build_results(scrape)
    assert ingest.run(api.db, results, file_hashes=hashes, now=now, no_school_rows=[]).ok
    assert data(api.get(BASE))["data"] == []
    assert [i["id"] for i in data(api.get(f"{BASE}?dismissed_only=1"))["data"]] == [1]
    item = data(api.get(f"{BASE}?include_closed=1"))["data"][0]
    assert (item["stage"], item["dismissed_at"], item["title"]) == (3, stamp, "Revised Essay")
    with readctx.connection(readonly=True) as conn:
        summary, digest_items = digest_run.assemble(conn, readctx.load(conn, now))
    assert digest_items == [] and summary["kids"][0]["counts"]["waiting_on_teacher"] == 0
    undo = {"action": "undo", "actor": ACTOR, "expected_dismissed_at": stamp}
    item = data(api.post(f"{BASE}/1/stage", undo))["data"]["item"]
    assert item["dismissed_at"] is None and item["stage"] == 3
    err(api.post(f"{BASE}/1/stage", undo), 409, "invalid_transition")
    assert data(api.get(BASE))["data"][0]["stage"] == 3
    assert data(api.get(f"{BASE}?include_closed=1&dismissed_only=1"))["data"] == []
    events = data(api.get(f"{BASE}/1"))["data"]["events"]
    edits = [(e["detail"], e["setter"]) for e in events if e["kind"] == "edit"]
    assert ("dismiss", "kid") in edits and ("undo_dismiss", "parent") in edits
    assert api.q("SELECT stage_since FROM assignments")[0][0] == before


@pytest.mark.parametrize("changes", [{"due_date": "2026-11-20"}, {"kind": "task"}, {"kind": "practice"}, {"stage": 4}])
def test_dismiss_requires_an_undated_open_assignment(api_factory, changes):
    api = api_factory([{**one()[0], "due_date": None, **changes}])
    err(stage(api, action="dismiss"), 409, "invalid_transition")


def test_dismiss_suppresses_pending_alerts_and_removes_missing_count(api_factory):
    api = api_factory([{**one()[0], "due_date": None, "portal_status": "missing"}])
    api.exec("INSERT INTO push_outbox(assignment_id, flag, occurrence_at, reason, status, created_at)"
             " VALUES (1, 'missing', ?, 'transition', 'pending', ?)", [api.clock["now"].isoformat()] * 2)
    result = data(stage(api, action="dismiss"))["data"]
    assert result["counts"]["needs_action"] == 0
    assert api.q("SELECT status FROM push_outbox")[0][0] == "suppressed"
    restored = data(api.post(f"{BASE}/1/stage", {"action": "undo", "actor": ACTOR,
                                               "expected_dismissed_at": result["item"]["dismissed_at"]}))["data"]
    assert restored["counts"]["needs_action_detail"]["missing"] == 1


def test_v2_remains_readable_and_first_dismiss_upgrades_it(api_factory):
    from school_dashboard.assignments import schema

    api = api_factory([{**one()[0], "due_date": None}])
    api.exec("ALTER TABLE assignments DROP COLUMN dismissed_at")
    api.exec("PRAGMA user_version=2")
    assert data(api.get(BASE))["data"][0]["dismissed_at"] is None
    assert api.q("PRAGMA user_version")[0][0] == 2
    assert stage(api, action="dismiss").status_code == 200
    assert api.q("PRAGMA user_version")[0][0] == schema.SCHEMA_VERSION


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
    assert r2.status_code == 200 and new["id"] == 2 and [ln["source"] for ln in new["links"]] == [
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
    err(api.post(f"{BASE}/1/split", {"link_id": 1, "actor": ACTOR}), 409, "last_link")


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
    from web import api_v2_push
    api = api_factory(one())
    monkeypatch.setenv("RECON_PUSH_ENABLED", "1")
    assert stage(api).status_code == 200 and api_v2_push.wait_idle(5) and len(calls) == 1
    api.get(f"{BASE}/1")
    assert api_v2_push.wait_idle(5) and len(calls) == 1  # reads never push
    err(stage(api, 99), 404, "not_found")
    assert api_v2_push.wait_idle(5) and len(calls) == 1  # failed writes never push


def test_failed_validation_changes_nothing(api_factory):
    api = api_factory(one())
    for body in ({"action": "bogus", "actor": ACTOR}, {"action": "done"}, {"action": "done", "actor": ACTOR, "x": 1}):
        assert api.post(f"{BASE}/1/stage", body).status_code == 400
    assert api.q("SELECT COUNT(*) AS n FROM assignment_events")[0]["n"] == 0
    assert api.q("SELECT stage FROM assignments")[0]["stage"] == 1
