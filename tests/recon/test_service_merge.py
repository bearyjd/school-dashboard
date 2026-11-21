"""merge / split / remerge / set_class, alias hook and report exclusion."""
import json
import logging

import pytest

from recon.report_support import ps, sgy
from recon.s4_support import NOW, essay_pair, event_rows, kid_actor, kw, link_set, open_conn, parent, stored
from school_dashboard.assignments import aliases, repo, report, service


def pair(tmp_path, a=None, b=None):
    sa, sb = essay_pair()
    return open_conn(tmp_path, [{**sa, **(a or {})}, {**sb, **(b or {})}])


def test_merge_moves_links_and_marks_source(tmp_path):
    c = pair(tmp_path)
    assert service.merge(c, 2, 1, parent("Dana"), **kw()) == 1
    assert link_set(c, 1) == {("schoology", "s1"), ("powerschool", "p1")} and link_set(c, 2) == set()
    src = stored(c, 2)
    assert src["merged_into"] == 1 and src["closed_at"] and src["flag"] is None
    assert stored(c, 1)["title"] == "Essay" and stored(c, 1)["merged_into"] is None
    for aid in (1, 2):
        ev = [e for e in event_rows(c, aid) if e["kind"] == "merge"][-1]
        assert (ev["detail"], ev["setter"], ev["actor"]) == ("manual links=2", "parent", "Dana")


@pytest.mark.parametrize("a,b,stage", [(2, 3, 3), (3, 1, 3), (1, 1, 1), (2, 2, 2)])
def test_merge_keeps_highest_manual_stage(tmp_path, a, b, stage):
    c = pair(tmp_path, {"stage": a}, {"stage": b})
    service.merge(c, 2, 1, parent(), **kw())
    assert stored(c, 1)["stage"] == stage


def test_merge_rederives_when_a_stage_4_side_is_involved(tmp_path):
    a = {"stage": 4, "received_evidence": "portal", "links": [
        {"source": "schoology", "external_key": "s1", "norm_status": "graded", "score": "9/10"}]}
    c = pair(tmp_path, a, {"stage": 2})
    service.merge(c, 2, 1, parent(), **kw())
    assert (stored(c, 1)["stage"], stored(c, 1)["received_evidence"]) == (4, "portal")


def test_merge_with_live_missing_link_reopens_a_portal_received_side(tmp_path):
    a = {"stage": 4, "received_evidence": "portal", "links": [
        {"source": "schoology", "external_key": "s1", "norm_status": "graded", "score": "9/10"}]}
    b = {"links": [{"source": "powerschool", "external_key": "p1", "norm_status": "missing"}]}
    c = pair(tmp_path, a, b)
    service.merge(c, 2, 1, parent(), **kw())
    row = stored(c, 1)
    assert row["stage"] == 1 and row["closed_at"] is None and row["portal_status"] == "missing"


@pytest.mark.parametrize("evidence", ["teacher_confirmed", "migration"])
def test_merge_refuses_teacher_confirmed_and_migration_evidence(tmp_path, evidence):
    # S4 review round: silently dropping a persistent stage 4 was a bug; the merge is refused instead
    c = pair(tmp_path, {"stage": 4, "received_evidence": evidence, "closed_at": "2026-11-17T12:00:00-05:00"})
    with pytest.raises(service.Conflict) as err:
        service.merge(c, 2, 1, parent(), **kw())
    assert err.value.code == "closed_record"
    row = stored(c, 1)
    assert (row["stage"], row["received_evidence"]) == (4, evidence) and row["closed_at"]


def test_merge_recomputes_the_target_flag(tmp_path):
    c = pair(tmp_path, {"due_date": "2026-11-10"}, {"stage": 3, "due_date": "2026-11-10"})
    c.execute("UPDATE assignments SET flag = 'missing' WHERE id = 1")
    service.merge(c, 2, 1, parent(), **kw())
    assert stored(c, 1)["flag"] == "waiting_on_teacher" or stored(c, 1)["flag"] == "done_not_received"


def test_merge_refusals(tmp_path):
    c = open_conn(tmp_path, [*essay_pair(), {**sgy("Other", "Math 6", "s3"), "kid": "Sam"},
                             {**sgy("Chore", "", "s4"), "kind": "task"}])
    for src, into, actor, code in [(2, 2, parent(), "same_record"), (3, 1, parent(), "different_kid"),
                                   (4, 1, parent(), "kind_mismatch"), (2, 1, kid_actor(), "forbidden_role")]:
        with pytest.raises(service.Conflict) as err:
            service.merge(c, src, into, actor, **kw())
        assert err.value.code == code
    with pytest.raises(service.NotFound):
        service.merge(c, 2, 99, parent(), **kw())
    service.merge(c, 2, 1, parent(), **kw())
    with pytest.raises(service.Conflict) as err:
        service.merge(c, 2, 1, parent(), **kw())
    assert err.value.code == "merged_away"


def test_merged_away_records_are_excluded_everywhere(tmp_path):
    c = pair(tmp_path)
    service.merge(c, 2, 1, parent(), **kw())
    assert [a.id for a in repo.list_assignments(c, "Alex", include_closed=True)] == [1]
    assert [a.state.id for a in repo.assignment_records(c)] == [1]
    inp = report.read_input(c)
    assert [r.id for r in inp.assignments] == [1]


def test_ingest_after_merge_links_to_the_target(tmp_path):
    from recon.ingest_support import build_results
    from school_dashboard.assignments import ingest
    c = open_conn(tmp_path)
    path = c.execute("PRAGMA database_list").fetchone()["file"]
    run = {"sgy": {"Alex": [{"title": "Essay", "course": "Math 6", "due": "2026-11-20"}]},
           "ps": {"Alex": {"late": [{"title": "Essay draft", "course": "Math 6", "due": "2026-11-20"}]}}}
    res, hashes = build_results(run)
    ingest.run(path, res, file_hashes=hashes, now=NOW, no_school_rows=[])
    ids = sorted(r[0] for r in c.execute("SELECT id FROM assignments"))
    assert len(ids) == 2
    service.merge(c, ids[1], ids[0], parent(), **kw())
    res, hashes = build_results(run)
    ingest.run(path, res, file_hashes=hashes, now=NOW, no_school_rows=[])
    assert c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 2
    assert len(link_set(c, ids[0])) == 2


def test_split_creates_a_stage_1_record_for_the_link(tmp_path):
    # a record that never absorbed this link by a manual merge: the link gets a new record
    both = {**sgy("Essay", "Math 6", "s1"), "links": [
        {"source": "schoology", "external_key": "s1", "norm_status": "open"},
        {"source": "powerschool", "external_key": "p1", "norm_status": "open"}]}
    c = open_conn(tmp_path, [both])
    lid = c.execute("SELECT id FROM source_links WHERE source = 'powerschool'").fetchone()[0]
    c.execute("UPDATE source_links SET raw_json = ? WHERE id = ?",
              (json.dumps({"title": "Other\x00 Thing", "course": "Science", "due": "2026-11-25"}), lid))
    new = service.split(c, 1, lid, parent("Dana"), **kw())
    row = stored(c, new)
    assert (row["title"], row["class_name"], row["due_date"], row["stage"]) == ("Other Thing", "Science", "2026-11-25", 1)
    assert link_set(c, new) == {("powerschool", "p1")} and link_set(c, 1) == {("schoology", "s1")}
    for aid in (1, new):
        ev = [e for e in event_rows(c, aid) if e["kind"] == "split"]
        assert ev and ev[-1]["setter"] == "parent" and ev[-1]["actor"] == "Dana"


def test_merge_then_split_restores_the_original_link_sets(tmp_path):
    c = pair(tmp_path)
    service.merge(c, 2, 1, parent(), **kw())
    lid = c.execute("SELECT id FROM source_links WHERE source = 'powerschool'").fetchone()[0]
    new = service.split(c, 1, lid, parent(), **kw())
    assert link_set(c, 1) == {("schoology", "s1")} and link_set(c, new) == {("powerschool", "p1")}
    assert stored(c, new)["title"] == "Essay"  # raw data empty: copied from the record


def test_split_refusals(tmp_path):
    c = pair(tmp_path)
    with pytest.raises(service.Conflict) as err:
        service.split(c, 1, 1, parent(), **kw())
    assert err.value.code == "last_link"
    service.merge(c, 2, 1, parent(), **kw())
    with pytest.raises(service.NotFound):
        service.split(c, 1, 999, parent(), **kw())
    with pytest.raises(service.NotFound):
        service.split(c, 99, 1, parent(), **kw())
    with pytest.raises(service.Conflict):
        service.split(c, 1, 1, kid_actor(), **kw())


def test_split_rederives_both_sides(tmp_path):
    a = {"stage": 4, "received_evidence": "portal", "links": [
        {"source": "schoology", "external_key": "s1", "norm_status": "open"},
        {"source": "powerschool", "external_key": "p9", "norm_status": "graded", "score": "9/10"}]}
    c = open_conn(tmp_path, [{**sgy("Essay", "Math 6", "s1"), **a}])
    lid = c.execute("SELECT id FROM source_links WHERE external_key = 'p9'").fetchone()[0]
    new = service.split(c, 1, lid, parent(), **kw())
    assert (stored(c, 1)["stage"], stored(c, 1)["closed_at"]) == (1, None)
    assert (stored(c, new)["stage"], stored(c, new)["received_evidence"]) == (4, "portal")


# ---- remerge ----
def alias_db(tmp_path, extra=()):
    seed = [sgy("Essay", "Algebra", "s1"), ps("Essay", "Math 6", "p1"), *extra]
    return open_conn(tmp_path, seed)


def add_alias(c, names=("Algebra",)):
    c.execute("INSERT INTO class_settings (kid, class_key, aliases) VALUES ('Alex', 'math 6', ?)",
              (json.dumps(list(names)),))


def test_remerge_merges_an_unambiguous_pair_after_alias(tmp_path):
    c = alias_db(tmp_path)
    add_alias(c)
    assert service.remerge_kid(c, "Alex", None, **kw()).merged == ((2, 1),)
    assert stored(c, 1)["class_key"] == "math 6" and stored(c, 2)["merged_into"] == 1
    ev = [e for e in event_rows(c, 1) if e["kind"] == "merge"][-1]
    assert (ev["setter"], ev["actor"]) == ("system", None)
    assert service.remerge_kid(c, "Alex", None, **kw()).merged == ()


def test_remerge_with_actor_attributes_events(tmp_path):
    c = alias_db(tmp_path)
    add_alias(c)
    service.remerge_kid(c, "Alex", parent("Dana"), **kw())
    ev = [e for e in event_rows(c, 1) if e["kind"] == "merge"][-1]
    assert (ev["setter"], ev["actor"]) == ("parent", "Dana")


def test_remerge_leaves_ambiguous_groups_alone(tmp_path):
    c = alias_db(tmp_path, [ps("Essay", "Math 6", "p2")])
    add_alias(c)
    assert service.remerge_kid(c, "Alex", None, **kw()).merged == ()
    assert all(stored(c, i)["merged_into"] is None for i in (1, 2, 3))


def test_remerge_needs_disjoint_sources_and_a_due_date(tmp_path):
    c = open_conn(tmp_path, [sgy("Essay", "Algebra", "s1"), sgy("Essay", "Math 6", "s2")])
    add_alias(c)
    assert service.remerge_kid(c, "Alex", None, **kw()).merged == ()
    (tmp_path / "b").mkdir()
    c2 = open_conn(tmp_path / "b",
                   [{**sgy("Essay", "Algebra", "s1"), "due_date": None},
                    {**ps("Essay", "Math 6", "p1"), "due_date": None}])
    add_alias(c2)
    assert service.remerge_kid(c2, "Alex", None, **kw()).merged == ()


def test_alias_add_triggers_remerge(tmp_path):
    c = alias_db(tmp_path)
    aliases.add(c, kid="Alex", from_name="Algebra", to_name="Math 6", now=NOW)
    assert stored(c, 2)["merged_into"] == 1


# ---- set_class ----
def cs(**o):
    return service.ClassSettings(**o)


def test_set_class_stores_validated_settings_and_logs_counts_only(tmp_path, caplog):
    c = alias_db(tmp_path)
    caplog.set_level(logging.INFO)
    service.set_class(c, "Alex", "Math 6", cs(grace_school_days=4, paper_only=True, aliases=("Algebra",),
                                              teacher_name="Ms Lee", teacher_email="lee@school.example"),
                      parent(), **kw())
    row = c.execute("SELECT * FROM class_settings WHERE kid = 'Alex' AND class_key = 'math 6'").fetchone()
    assert (row["grace_school_days"], row["paper_only"], row["teacher_name"]) == (4, 1, "Ms Lee")
    assert json.loads(row["aliases"]) == ["Algebra"]
    assert stored(c, 2)["merged_into"] == 1  # alias change re-merged
    assert "Algebra" not in caplog.text and "Math 6" not in caplog.text and "Alex" not in caplog.text


@pytest.mark.parametrize("settings", [cs(grace_school_days=11), cs(grace_school_days=-1),
                                      cs(aliases=tuple(f"a{i}" for i in range(21))), cs(aliases=("Math 6",)),
                                      cs(aliases=("x" * 101,)), cs(aliases=("\x00",)), cs(teacher_email="nope"),
                                      cs(teacher_email="a@b.c" + "x" * 200), cs(teacher_name="n" * 121)])
def test_set_class_rejects_bad_settings(tmp_path, settings):
    c = alias_db(tmp_path)
    with pytest.raises(service.Conflict):
        service.set_class(c, "Alex", "Math 6", settings, parent(), **kw())
    assert c.execute("SELECT COUNT(*) FROM class_settings").fetchone()[0] == 0


def test_set_class_refusals(tmp_path):
    c = alias_db(tmp_path)
    with pytest.raises(service.Conflict):
        service.set_class(c, "Alex", "Math 6", cs(), kid_actor(), **kw())
    with pytest.raises(service.NotFound):
        service.set_class(c, "Zed", "Math 6", cs(), parent(), **kw())


def test_set_class_grace_change_recomputes_flags(tmp_path):
    c = open_conn(tmp_path, [sgy("Essay", "Math 6", "s1", stage=2, stage_since="2026-11-17T12:00:00-05:00",
                                 due="2026-11-10")])
    c.execute("UPDATE assignments SET flag = 'waiting_on_teacher' WHERE id = 1")
    service.set_class(c, "Alex", "Math 6", cs(grace_school_days=0), parent(), **kw())
    assert stored(c, 1)["flag"] == "done_not_received"


# ---- report ----
def test_report_shows_schema_and_excludes_merged(tmp_path):
    c = pair(tmp_path)
    service.merge(c, 2, 1, parent(), **kw())
    path = c.execute("PRAGMA database_list").fetchone()["file"]
    rep = report.run(path, now=NOW, meta={})
    assert rep["schema"] == {"version": 2, "legacy_rows_outside_limits": 0}
    assert rep["kids"]["Alex"]["assignments"] == 1
    assert "schema: v2, 0 legacy rows outside the new limits" in report.render_text(rep)
