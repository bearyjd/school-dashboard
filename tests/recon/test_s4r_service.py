"""S4 review round (service/domain): merge safety, split restore, undo, audit, debounce, scores, pins."""
import dataclasses
import json
import sqlite3
from collections import Counter
from datetime import timedelta

import pytest

from recon.adapter_support import resolve
from recon.report_support import ps, sgy
from recon.s4_support import NOW, event_rows, kw, link_set, open_conn, parent, kid_actor, stored
from recon.support import mk_link, mk_state
from school_dashboard.assignments import aliases, repo, schema, service, stages

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
    assert repo.latest_manual_stage(c, 1) == 1
    _manual(c, 1, 4)
    assert repo.latest_manual_stage(c, 1) == 1  # never 4 from manual history
    _manual(c, 1, 3)
    _manual(c, 1, 2, setter="portal")
    assert repo.latest_manual_stage(c, 1) == 3
    _manual(c, 1, 2, setter="kid")
    assert repo.latest_manual_stage(c, 1) == 2
    _manual(c, 1, 3, detail="merge")
    assert repo.latest_manual_stage(c, 1) == 3
    assert repo.latest_manual_stage(c, 1, ignore_details=("merge",)) == 2
    _manual(c, 1, 1, detail="undo")
    assert repo.latest_manual_stage(c, 1) == 1


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


# ---- C1 ----
def d(score, status="graded", **kw_):
    return stages.portal_decision(mk_state(), [mk_link(norm_status=status, score=score, **kw_)], NOW.date())


@pytest.mark.parametrize("score", ["95", "7/10", "0.5", ".5", "A", "b+", "C-", "D", "p", "Pass", "PASS",
                                   "Score: 8/10", "85%", "10 / 10"])
@pytest.mark.parametrize("status", ["graded", "submitted"])
def test_real_scores_count_on_submitted_or_graded(score, status):
    assert d(score, status) == (4, "portal")


@pytest.mark.parametrize("score", ["0", "0/10", "0%", "0.0", "-/10", "-", "--", "—", "–", "n/a", "N/A",
                                   "na", "missing", "m", "exempt", "excused", "incomplete", "inc", "late", "F",
                                   "f", "", "  ", None, "-5", "E", "x/10"])
def test_non_evidence_scores_do_not_count(score):
    assert d(score)[0] == 3 and d(score, "submitted")[0] == 3


@pytest.mark.parametrize("status", ["late", "missing", "open", "none", "cleared"])
def test_a_score_on_other_statuses_is_never_receipt_evidence(status):
    assert d("9/10", status)[0] != 4


def test_real_score_text_function():
    assert stages._real_score("9/10") and not stages._real_score("0/10") and not stages._real_score(None)


def test_powerschool_late_row_with_zero_score_stays_late():
    from school_dashboard.assignments.adapters.powerschool import PowerSchoolAdapter
    item = {"course": "Math", "title": "T", "due": "2026-11-06", "category": None, "score": "0/10"}
    entry = {"name": "Alpha", "missing": [], "late": [item], "upcoming": [], "warnings": []}
    got = PowerSchoolAdapter().parse({"children": [entry], "warnings": []}, resolve_kid=resolve)
    assert [r.portal_status for r in got.records] == ["late"]


# ---- C3 ----
@pytest.mark.parametrize("url", [
    "https://x.org/a?assignment_id=5", "https://x.org/?monkey=1", "https://x.org/?keyword=1",
    "https://x.org/p?sidebar=1&id=3", "https://x.org/p;version=2", "https://x.org/?authority=1"])
def test_safe_url_keeps_harmless_urls(url):
    from school_dashboard.assignments.readmodel import safe_url
    assert safe_url(url) == url


@pytest.mark.parametrize("url", [
    "https://x.org/?token=a", "https://x.org/?TOKEN=a", "https://x.org/?api_key=a", "https://x.org/?api-key=a",
    "https://x.org/?key=a", "https://x.org/?auth_token=a", "https://x.org/?access-token=a",
    "https://x.org/?session=a", "https://x.org/?sig=a", "https://x.org/?X-Amz-Signature=a",
    "https://x.org/?password=a", "https://x.org/?secret_value=a", "https://x.org/#access_token=abc",
    "https://x.org/a;jsessionid=abc", "https://x.org/a;sessionid=abc/b", "https://x.org/a;token=1",
    "https://u:p@x.org/", "ftp://x.org/", "https://x.org/?sid=1", "javascript:alert(1)"])
def test_safe_url_drops_credential_urls(url):
    from school_dashboard.assignments.readmodel import safe_url
    assert safe_url(url) is None


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


# ---- S2a debounce ----
def _ctx(c, now, baseline=False):
    from school_dashboard.assignments import ingest_kid
    return ingest_kid.build_context(c, "Alex", now=now, no_school_rows=[], baseline=baseline, file_hashes={})


def _rows(c, flag="missing"):
    return c.execute("SELECT status, reason FROM push_outbox WHERE assignment_id = 1 AND flag = ?", (flag,)).fetchall()


def test_enqueue_is_debounced_for_six_hours(tmp_path):
    from school_dashboard.assignments import ingest_status
    c = two(tmp_path)
    counts = Counter()
    for h in range(6):
        ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=h)), 1, "missing", counts)
    assert len(_rows(c)) == 1 and counts["pushes_enqueued"] == 1
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=6, minutes=1)), 1, "missing", counts)
    assert len(_rows(c)) == 2 and counts["pushes_enqueued"] == 2
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=1)), 1, "disputed", counts)
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=1)), 2, "missing", counts)
    assert len(_rows(c, "disputed")) == 1
    assert c.execute("SELECT COUNT(*) FROM push_outbox WHERE assignment_id = 2").fetchone()[0] == 1


@pytest.mark.parametrize("status", ["sent", "failed", "pending"])
def test_any_recent_status_debounces(tmp_path, status):
    from school_dashboard.assignments import ingest_status
    c = two(tmp_path)
    repo.enqueue_push(c, 1, "missing", NOW, "transition")
    c.execute("UPDATE push_outbox SET status = ?", (status,))
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=2)), 1, "missing", Counter())
    assert len(_rows(c)) == 1


def test_baseline_suppressed_rows_do_not_debounce(tmp_path):
    from school_dashboard.assignments import ingest_status
    c = two(tmp_path)
    ingest_status.enqueue_debounced(c, _ctx(c, NOW, baseline=True), 1, "missing", Counter())
    assert [tuple(r) for r in _rows(c)] == [("suppressed", "baseline")]
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=1)), 1, "missing", Counter())
    assert [tuple(r) for r in _rows(c)] == [("suppressed", "baseline"), ("pending", "transition")]


def test_debounce_compares_across_a_dst_offset_change(tmp_path):
    from school_dashboard.assignments import ingest_status
    c = two(tmp_path)
    t0 = NOW.replace(month=11, day=1, hour=0)  # EDT (-04:00)
    repo.enqueue_push(c, 1, "missing", t0, "transition")
    later = (t0 + timedelta(hours=7)).astimezone(NOW.tzinfo)
    ingest_status.enqueue_debounced(c, _ctx(c, later), 1, "missing", Counter())
    assert len(_rows(c)) == 2


# ---- C6 pins ----
def test_assignments_and_links_cannot_be_deleted(tmp_path):
    c = two(tmp_path)
    for sql in ("DELETE FROM assignments WHERE id = 1", "DELETE FROM source_links"):
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql)
    assert c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 2


def _rec(raw):
    from recon.support import mk_record
    return mk_record(raw=raw)


@pytest.mark.parametrize("size,kept", [(8191, True), (8192, True), (8193, False)])
def test_raw_json_8_kib_boundary(size, kept):
    from school_dashboard.assignments import ingest_kid
    overhead = len(json.dumps({"a": ""}))
    out = ingest_kid._raw_json(_rec({"a": "x" * (size - overhead)}))
    assert len(json.dumps({"a": "x" * (size - overhead)})) == size
    assert (json.loads(out).get("truncated") is None) is kept
    if not kept:
        assert json.loads(out) == {"truncated": True, "bytes": size}


def _big(i, title="\u20ac" * 200):
    return {"id": i, "kid": "\u20ac" * 5, "title": title, "class_name": None, "flag": "missing"}


def _size(row):
    from school_dashboard.assignments import notify
    return len(notify._line(row).encode("utf-8")) + 1


@pytest.mark.parametrize("filler", ["\u20ac", "\u00e9", "e"])
def test_push_body_never_exceeds_the_byte_bound_and_is_maximal(filler):
    from school_dashboard.assignments import notify
    reserve = len("\n+99999 more")
    hit_limit = 0
    for k in range(1, 110):
        rows = [_big(i) for i in range(9)] + [_big(9, filler * k)]
        chosen, msg = notify._batch(rows)
        assert len(msg.encode("utf-8")) <= notify.MAX_BODY_BYTES
        if len(chosen) < len(rows):
            used = sum(_size(r) for r in chosen)
            assert used + _size(rows[len(chosen)]) + reserve > notify.MAX_BODY_BYTES  # one more would not fit
            assert msg.splitlines()[-1] == f"+{len(rows) - len(chosen)} more"
            hit_limit += 1
    assert notify._MORE_RESERVE == reserve and (hit_limit > 0 or filler != "\u20ac")


def test_a_row_exactly_at_the_limit_is_taken():
    from school_dashboard.assignments import notify
    rows = [_big(i) for i in range(9)]
    room = notify.MAX_BODY_BYTES - notify._MORE_RESERVE - sum(_size(r) for r in rows)
    extra = room - _size(_big(9, ""))
    title = "\u20ac" * (extra // 3) + ("", "e", "\u00e9")[extra % 3]
    last = _big(9, title)
    assert _size(last) == room
    chosen, _ = notify._batch([*rows, last])
    assert len(chosen) == 10
    chosen, msg = notify._batch([*rows, _big(9, title + "e")])
    assert len(chosen) == 9 and msg.endswith("+1 more")
