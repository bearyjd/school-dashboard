"""S4 review round (service/domain): merge safety, split restore, undo, audit, debounce, scores, pins."""
import dataclasses
import json
import sqlite3

import pytest

from recon.report_support import ps, sgy
from recon.s4_support import NOW, event_rows, kw, link_set, open_conn, parent, kid_actor, stored
from school_dashboard.assignments import aliases, repo, repo_history, schema, service

CONFIRMED = {"stage": 4, "received_evidence": "teacher_confirmed", "closed_at": "2026-11-17T12:00:00-05:00"}


def two(tmp_path, a=None, b=None, cls_b="Math 6"):
    return open_conn(tmp_path, [{**sgy("Essay", "Math 6", "s1"), **(a or {})},
                                {**ps("Essay", cls_b, "p1"), **(b or {})}])


def code_of(fn, *a, **k):
    with pytest.raises(service.Conflict) as err:
        fn(*a, **k)
    return err.value.code


def lid(c, source):
    return c.execute("SELECT id FROM source_links WHERE source = ?", (source,)).fetchone()[0]


# ---- S4a ----
def test_merge_of_different_classes_needs_force(tmp_path):
    c = two(tmp_path, cls_b="Science")
    assert code_of(service.merge, c, 2, 1, parent(), **kw()) == "class_mismatch"
    assert link_set(c, 2) == {("powerschool", "p1")} and stored(c, 2)["merged_into"] is None
    service.merge(c, 2, 1, parent(), force=True, **kw())
    assert stored(c, 2)["merged_into"] == 1
    for aid in (1, 2):
        assert [e for e in event_rows(c, aid) if e["kind"] == "merge"][-1]["detail"].startswith("manual forced")


def test_merge_of_two_unclassed_records_needs_force(tmp_path):
    c = open_conn(tmp_path, [sgy("Essay", "", "s1"), ps("Essay", "", "p1")])
    assert code_of(service.merge, c, 2, 1, parent(), **kw()) == "class_mismatch"
    service.merge(c, 2, 1, parent(), force=True, **kw())


def test_unforced_merge_of_the_same_class_records_plain_manual(tmp_path):
    c = two(tmp_path)
    service.merge(c, 2, 1, parent(), **kw())
    ev = [e for e in event_rows(c, 1) if e["kind"] == "merge"][-1]
    assert ev["detail"] == f"manual links={lid(c, 'powerschool')}"


# ---- S4b ----
@pytest.mark.parametrize("evidence", ["teacher_confirmed", "migration"])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("src,into", [(2, 1), (1, 2)])
def test_merge_with_a_persistently_closed_record_is_refused(tmp_path, evidence, force, src, into):
    poster = {**sgy("Poster", "Math 6", "s1"), **CONFIRMED, "received_evidence": evidence}
    big = {**ps("Big Test", "Math 6", "p1", status="missing")}
    c = open_conn(tmp_path, [poster, big])
    assert code_of(service.merge, c, src, into, parent(), force=force, **kw()) == "closed_record"
    assert (stored(c, 1)["stage"], stored(c, 1)["received_evidence"]) == (4, evidence)
    assert link_set(c, 1) == {("schoology", "s1")} and link_set(c, 2) == {("powerschool", "p1")}
    assert stored(c, 2)["merged_into"] is None and stored(c, 1)["merged_into"] is None


def test_merge_into_a_reopened_record_works(tmp_path):
    c = open_conn(tmp_path, [{**sgy("Poster", "Math 6", "s1"), **CONFIRMED}, ps("Big Test", "Math 6", "p1")])
    service.tap(c, 1, "undo", parent(), **kw())
    service.merge(c, 2, 1, parent(), **kw())
    assert stored(c, 1)["stage"] == 3 and stored(c, 2)["merged_into"] == 1


def test_remerge_skips_pairs_with_a_closed_record(tmp_path):
    c = open_conn(tmp_path, [{**sgy("Essay", "Algebra", "s1"), **CONFIRMED}, ps("Essay", "Math 6", "p1")])
    c.execute("INSERT INTO class_settings (kid, class_key, aliases) VALUES ('Alex', 'math 6', ?)",
              (json.dumps(["Algebra"]),))
    assert service.remerge_kid(c, "Alex", None, **kw()).merged == ()


# ---- S4c ----
def test_too_many_links_refuses_the_merge(tmp_path):
    many = {"links": [{"source": "powerschool", "external_key": f"k{i}", "norm_status": "open"}
                      for i in range(150)]}
    c = two(tmp_path, b=many)
    assert code_of(service.merge, c, 2, 1, parent(), **kw()) == "too_many_links"
    assert len(link_set(c, 2)) == 150 and stored(c, 2)["merged_into"] is None


def test_merge_event_lists_every_moved_link(tmp_path):
    b = {"links": [{"source": "powerschool", "external_key": f"k{i}", "norm_status": "open"} for i in range(3)]}
    c = two(tmp_path, b=b)
    moved = sorted(r[0] for r in c.execute("SELECT id FROM source_links WHERE assignment_id = 2"))
    service.merge(c, 2, 1, parent(), **kw())
    detail = "manual links=" + ",".join(map(str, moved))
    for aid in (1, 2):
        assert [e for e in event_rows(c, aid) if e["kind"] == "merge"][-1]["detail"] == detail


# ---- S4d ----
def test_merge_then_split_restores_the_merged_away_record(tmp_path):
    c = two(tmp_path)
    service.tap(c, 1, "done", parent(), **kw())
    service.tap(c, 2, "turned_in", parent(), **kw())
    service.merge(c, 2, 1, parent(), **kw())
    assert stored(c, 1)["stage"] == 3
    before = {a: len(event_rows(c, a)) for a in (1, 2)}
    new = service.split(c, 1, lid(c, "powerschool"), parent("Dana"), **kw())
    assert new == 2 and c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 2
    two_row, one_row = stored(c, 2), stored(c, 1)
    assert two_row["merged_into"] is None and two_row["closed_at"] is None and two_row["stage"] == 3
    assert one_row["stage"] == 2 and one_row["closed_at"] is None
    assert link_set(c, 1) == {("schoology", "s1")} and link_set(c, 2) == {("powerschool", "p1")}
    for aid in (1, 2):
        evs = event_rows(c, aid)
        assert len(evs) > before[aid] and any(e["kind"] == "merge" for e in evs)
        assert [e for e in evs if e["kind"] == "split"][-1]["actor"] == "Dana"
    assert [a.id for a in repo.list_assignments(c, "Alex", True)] == [1, 2]


def test_split_of_a_never_merged_link_still_creates_a_new_record(tmp_path):
    seed = {**sgy("Essay", "Math 6", "s1"), "links": [
        {"source": "schoology", "external_key": "s1", "norm_status": "open"},
        {"source": "powerschool", "external_key": "p1", "norm_status": "open"}]}
    c = open_conn(tmp_path, [seed])
    new = service.split(c, 1, lid(c, "powerschool"), parent(), **kw())
    assert new == 2 and stored(c, 2)["stage"] == 1 and link_set(c, 2) == {("powerschool", "p1")}


def test_split_restores_only_the_link_that_came_from_the_merge(tmp_path):
    c = two(tmp_path)
    service.merge(c, 2, 1, parent(), **kw())
    c.execute("INSERT INTO source_links (assignment_id, source, kid_external, external_key, norm_status, raw_json,"
              " first_seen, last_seen, trusted_misses, live, via) VALUES (1, 'gamechanger', 'Alex|g', 'g',"
              " 'open', '{}', 'x', 'x', 0, 1, 'scrape')")
    new = service.split(c, 1, lid(c, "gamechanger"), parent(), **kw())
    assert new == 3 and stored(c, 2)["merged_into"] == 1


# ---- S4e ----
def test_a_manually_split_pair_is_not_remerged_by_an_alias(tmp_path):
    c = two(tmp_path)
    service.merge(c, 2, 1, parent(), **kw())
    service.split(c, 1, lid(c, "powerschool"), parent(), **kw())
    aliases.add(c, kid="Alex", from_name="Algebra", to_name="Math 6", now=NOW)
    assert stored(c, 1)["merged_into"] is None and stored(c, 2)["merged_into"] is None
    assert service.remerge_kid(c, "Alex", None, **kw()).merged == ()


def test_a_split_off_record_is_not_remerged(tmp_path):
    seed = {**sgy("Essay", "Math 6", "s1"), "links": [
        {"source": "schoology", "external_key": "s1", "norm_status": "open"},
        {"source": "powerschool", "external_key": "p1", "norm_status": "open"}]}
    c = open_conn(tmp_path, [seed])
    service.split(c, 1, lid(c, "powerschool"), parent(), **kw())
    assert service.remerge_kid(c, "Alex", None, **kw()).merged == ()
    assert stored(c, 2)["merged_into"] is None


def test_ids_with_a_shared_prefix_do_not_block_other_pairs(tmp_path):
    c = open_conn(tmp_path, [sgy("A", "Math 6", "s1"), ps("A", "Math 6", "p1")])
    c.execute("INSERT INTO assignment_events (assignment_id, at, setter, kind, detail)"
              " VALUES (1, 'x', 'parent', 'split', 'manual with=22 link=9')")
    assert service.remerge_kid(c, "Alex", None, **kw()).merged == ((2, 1),)


# ---- C6 RemergeResult ----
def test_remerge_result_is_frozen_and_counts_ambiguous_groups(tmp_path):
    c = open_conn(tmp_path, [sgy("Essay", "Algebra", "s1"), ps("Essay", "Math 6", "p1"),
                             ps("Essay", "Math 6", "p2")])
    c.execute("INSERT INTO class_settings (kid, class_key, aliases) VALUES ('Alex', 'math 6', ?)",
              (json.dumps(["Algebra"]),))
    res = service.remerge_kid(c, "Alex", None, **kw())
    assert res == service.RemergeResult(merged=(), ambiguous_groups=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(res, "merged", ())


# ---- S5 ----
def test_parent_can_undo_a_teacher_confirmation(tmp_path):
    c = open_conn(tmp_path, [{**sgy("Poster", "Math 6", "s1"), **CONFIRMED}])
    service.tap(c, 1, "undo", parent("Dana"), **kw())
    row = stored(c, 1)
    assert (row["stage"], row["received_evidence"], row["closed_at"]) == (3, None, None)
    ev = [e for e in event_rows(c, 1) if e["kind"] == "stage"][-1]
    assert (ev["from_value"], ev["to_value"], ev["setter"], ev["actor"]) == ("4", "3", "parent", "Dana")


@pytest.mark.parametrize("seed,actor,action", [
    (CONFIRMED, kid_actor(), "undo"),
    ({**CONFIRMED, "received_evidence": "portal"}, parent(), "undo"),
    ({**CONFIRMED, "received_evidence": "migration"}, parent(), "undo"),
    (CONFIRMED, parent(), "done"), (CONFIRMED, parent(), "turned_in")])
def test_other_stage_4_taps_stay_refused(tmp_path, seed, actor, action):
    c = open_conn(tmp_path, [{**sgy("Poster", "Math 6", "s1"), **seed}])
    assert code_of(service.tap, c, 1, action, actor, **kw()) == "invalid_transition"
    assert stored(c, 1)["stage"] == 4


def test_undo_api(api_factory):
    from recon.api_support import ACTOR, KID_ACTOR
    api = api_factory([{**sgy("Poster", "Math 6", "s1"), **CONFIRMED}])
    r = api.post("/api/v2/assignments/1/stage", {"action": "undo", "actor": KID_ACTOR})
    assert r.status_code == 409
    r = api.post("/api/v2/assignments/1/stage", {"action": "undo", "actor": ACTOR})
    assert r.status_code == 200 and r.get_json()["data"]["item"]["stage"] == 3


# ---- C4 ----
def _manual(c, aid, to, setter="parent", detail="done"):
    repo.append_event(c, aid, at=NOW, setter=setter, kind="stage", to_value=str(to), detail=detail)


def test_latest_manual_stage_rules(tmp_path):
    c = two(tmp_path)
    assert repo_history.latest_manual_stage(c, 1) == 1
    _manual(c, 1, 4)
    assert repo_history.latest_manual_stage(c, 1) == 1  # never 4 from manual history
    _manual(c, 1, 3)
    _manual(c, 1, 2, setter="portal")
    assert repo_history.latest_manual_stage(c, 1) == 3
    _manual(c, 1, 2, setter="kid")
    assert repo_history.latest_manual_stage(c, 1) == 2
    _manual(c, 1, 3, detail="merge")
    assert repo_history.latest_manual_stage(c, 1) == 3
    assert repo_history.latest_manual_stage(c, 1, ignore_details=("merge",)) == 2
    _manual(c, 1, 1, detail="undo")
    assert repo_history.latest_manual_stage(c, 1) == 1


def _two_link_record(extra=None):
    return {**sgy("Essay", "Math 6", "s1"), **(extra or {}), "links": [
        {"source": "schoology", "external_key": "s1", "norm_status": "open"},
        {"source": "powerschool", "external_key": "p9", "norm_status": "graded", "score": "9/10"}]}


def test_split_keeps_a_manual_stage_when_portal_received_is_not_re_earned(tmp_path):
    c = open_conn(tmp_path, [_two_link_record({"stage": 4, "received_evidence": "portal"})])
    _manual(c, 1, 3)
    new = service.split(c, 1, lid(c, "powerschool"), parent(), **kw())
    assert stored(c, 1)["stage"] == 3 and stored(c, 1)["closed_at"] is None
    assert (stored(c, new)["stage"], stored(c, new)["received_evidence"]) == (4, "portal")


def test_split_drops_a_stale_portal_derived_stage_but_never_a_manual_one(tmp_path):
    rec = {**sgy("Essay", "Math 6", "s1"), "stage": 3, "links": [
        {"source": "schoology", "external_key": "s1", "norm_status": "open"},
        {"source": "powerschool", "external_key": "p9", "norm_status": "submitted"}]}
    c = open_conn(tmp_path, [rec])
    service.split(c, 1, lid(c, "powerschool"), parent(), **kw())
    assert stored(c, 1)["stage"] == 1


# ---- S3 audit ----
def audit(c):
    return [dict(r) for r in c.execute("SELECT * FROM class_settings_events ORDER BY id")]


def test_set_class_writes_one_audit_event_with_changed_fields_only(tmp_path):
    c = two(tmp_path)
    cs = service.ClassSettings(grace_school_days=3, paper_only=True)
    service.set_class(c, "Alex", "Math 6", cs, parent("Dana"), **kw())
    (ev,) = audit(c)
    assert (ev["kid"], ev["class_key"], ev["setter"], ev["actor"], ev["at"]) == (
        "Alex", "math 6", "parent", "Dana", NOW.isoformat())
    assert json.loads(ev["changes"]) == {"grace_school_days": [None, 3], "paper_only": [False, True]}
    service.set_class(c, "Alex", "Math 6", cs, parent("Dana"), **kw())
    assert len(audit(c)) == 1
    service.set_class(c, "Alex", "Math 6", dataclasses.replace(cs, aliases=("Algebra",), teacher_name="Ms Lee"),
                      parent("Dana"), **kw())
    assert json.loads(audit(c)[1]["changes"]) == {"aliases": [[], ["Algebra"]], "teacher_name": [None, "Ms Lee"]}


def test_audit_changes_stay_within_2000_chars(tmp_path):
    c = two(tmp_path)
    a = tuple(f"a{i:02d}" + "x" * 90 for i in range(15))
    b = tuple(f"b{i:02d}" + "y" * 90 for i in range(15))
    service.set_class(c, "Alex", "Math 6", service.ClassSettings(aliases=a), parent(), **kw())
    service.set_class(c, "Alex", "Math 6", service.ClassSettings(aliases=b), parent(), **kw())
    for ev in audit(c):
        assert len(ev["changes"]) <= 2000 and set(json.loads(ev["changes"])) == {"aliases"}


def test_alias_add_is_audited_once(tmp_path):
    c = two(tmp_path)
    aliases.add(c, kid="Alex", from_name="Algebra", to_name="Math 6", now=NOW)
    aliases.add(c, kid="Alex", from_name="Algebra", to_name="Math 6", now=NOW)
    (ev,) = audit(c)
    assert (ev["setter"], ev["actor"], ev["class_key"]) == ("system", None, "math 6")
    assert json.loads(ev["changes"]) == {"aliases": [[], ["Algebra"]]}


def test_audit_table_is_append_only(tmp_path):
    c = two(tmp_path)
    service.set_class(c, "Alex", "Math 6", service.ClassSettings(grace_school_days=1), parent(), **kw())
    for sql in ("UPDATE class_settings_events SET kid = 'x'", "DELETE FROM class_settings_events"):
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO class_settings_events (at, kid, class_key, setter, changes)"
                  " VALUES ('x', 'a', 'b', 'parent', ?)", ("x" * 2001,))


def test_a_v2_database_without_the_audit_table_is_upgraded_in_place(tmp_path):
    c = two(tmp_path)
    c.execute("DROP TABLE class_settings_events")
    assert schema.user_version(c) == schema.SCHEMA_VERSION
    service.set_class(c, "Alex", "Math 6", service.ClassSettings(grace_school_days=2), parent(), **kw())
    assert len(audit(c)) == 1
    schema.ensure_schema(c)  # idempotent
    assert len(audit(c)) == 1


def test_partial_class_settings_keep_stored_values(tmp_path):
    c = two(tmp_path)
    full = service.ClassSettings(grace_school_days=3, paper_only=True, aliases=("Algebra",), teacher_name="Ms Lee",
                                 teacher_email="lee@example.org")
    service.set_class(c, "Alex", "Math 6", full, parent(), **kw())
    part = service.ClassSettings(teacher_name=None, fields=frozenset({"teacher_name"}))
    service.set_class(c, "Alex", "Math 6", part, parent(), **kw())
    row = c.execute("SELECT * FROM class_settings").fetchone()
    assert (row["grace_school_days"], row["paper_only"], row["teacher_name"], row["teacher_email"]) == (
        3, 1, None, "lee@example.org") and json.loads(row["aliases"]) == ["Algebra"]


def test_service_rejects_oversized_alias_lists(tmp_path):
    c = two(tmp_path)
    big = tuple(f"{i:02d}" + "x" * 98 for i in range(20))
    assert code_of(service.set_class, c, "Alex", "Math 6", service.ClassSettings(aliases=big), parent(),
                   **kw()) == "invalid_class_settings"
    with pytest.raises(aliases.AliasError):
        for i in range(30):
            aliases.add(c, kid="Alex", from_name=f"{i:02d}" + "z" * 98, to_name="Math 6", now=NOW)
