"""stages.py: taps, register_scrape, portal_decision."""
import itertools
from datetime import date

import pytest

from recon.support import local, mk_link, mk_state
from school_dashboard.assignments import stages

NOW = local(2026, 11, 18, 17, 0)
TODAY = date(2026, 11, 20)


# ---- apply_tap ----
@pytest.mark.parametrize("stage,action,setter,expected", [
    (1, "done", "parent", 2), (1, "done", "kid", 2),
    (1, "turned_in", "parent", 3), (2, "turned_in", "kid", 3),
    (2, "undo", "parent", 1), (3, "undo", "kid", 1),
])
def test_apply_tap_moves_to_expected_stage(stage, action, setter, expected):
    assert stages.apply_tap(mk_state(stage=stage), action, setter, NOW).stage == expected


def test_apply_tap_sets_stage_since_to_now():
    assert stages.apply_tap(mk_state(), "done", "kid", NOW).stage_since == NOW


def test_apply_tap_returns_new_state_and_leaves_input_alone():
    old = mk_state()
    new = stages.apply_tap(old, "done", "kid", NOW)
    assert new is not old and old.stage == 1


@pytest.mark.parametrize("setter", ["portal", "system", "email", "migration", "teacher", "", "PARENT"])
def test_apply_tap_rejects_non_parent_kid_setters(setter):
    with pytest.raises(stages.StageError):
        stages.apply_tap(mk_state(), "done", setter, NOW)


@pytest.mark.parametrize("action", ["done", "turned_in", "undo"])
@pytest.mark.parametrize("setter", ["parent", "kid"])
def test_apply_tap_refuses_any_attempt_on_stage_4(action, setter):
    with pytest.raises(stages.StageError):
        stages.apply_tap(mk_state(stage=4, received_evidence="portal"), action, setter, NOW)


def test_apply_tap_rejects_unknown_action():
    with pytest.raises(stages.StageError):
        stages.apply_tap(mk_state(), "received", "parent", NOW)


def test_apply_tap_rejects_undo_at_stage_1():
    with pytest.raises(stages.StageError):
        stages.apply_tap(mk_state(stage=1), "undo", "parent", NOW)


def test_stage_error_is_a_value_error():
    assert issubclass(stages.StageError, ValueError)


def test_no_parent_or_kid_tap_sequence_reaches_stage_4():
    actions = [(a, s) for a in ("done", "turned_in", "undo", "received", "confirm") for s in ("parent", "kid")]
    reached = set()
    for seq in itertools.product(actions, repeat=4):
        st = mk_state()
        for action, setter in seq:
            try:
                st = stages.apply_tap(st, action, setter, NOW)
            except stages.StageError:
                continue
            reached.add(st.stage)
    assert 4 not in reached and reached == {1, 2, 3}


# ---- register_scrape ----
def scrape(link, **kw):
    base = dict(seen=False, trusted=True, file_hash="h1", today=TODAY)
    return stages.register_scrape(link, **{**base, **kw})


def test_seen_link_becomes_live_and_resets_misses():
    link = mk_link(live=False, trusted_misses=2, absent_since=date(2026, 11, 18), last_miss_hash="h0")
    out = scrape(link, seen=True, new_status="open")
    assert (out.live, out.trusted_misses, out.absent_since) == (True, 0, None)


def test_seen_link_takes_new_status_and_score():
    out = scrape(mk_link(), seen=True, new_status="graded", new_score="95")
    assert (out.norm_status, out.score) == ("graded", "95")


def test_seen_link_without_new_status_keeps_old_one():
    assert scrape(mk_link(norm_status="submitted"), seen=True).norm_status == "submitted"


def test_untrusted_unseen_scrape_changes_nothing():
    link = mk_link()
    assert scrape(link, trusted=False) == link


def test_trusted_unseen_with_same_hash_as_last_miss_changes_nothing():
    link = mk_link(live=False, trusted_misses=1, absent_since=date(2026, 11, 18), last_miss_hash="h1")
    assert scrape(link, file_hash="h1") == link


def test_trusted_unseen_new_hash_counts_one_miss_and_goes_absent():
    out = scrape(mk_link(), file_hash="h1")
    assert (out.live, out.trusted_misses, out.absent_since, out.last_miss_hash) == (False, 1, TODAY, "h1")


def test_second_miss_keeps_original_absent_since():
    first = scrape(mk_link(), file_hash="h1", today=date(2026, 11, 18))
    second = scrape(first, file_hash="h2", today=date(2026, 11, 20))
    assert (second.trusted_misses, second.absent_since) == (2, date(2026, 11, 18))


def test_screenshot_link_never_accrues_misses():
    assert scrape(mk_link(via="screenshot")).trusted_misses == 0


def test_missing_link_keeps_its_last_status_when_absent():
    assert scrape(mk_link(norm_status="missing")).norm_status == "missing"


# ---- portal_decision ----
def decide(state_kw=None, links=(), today=TODAY):
    return stages.portal_decision(mk_state(**(state_kw or {})), list(links), today)


def gone(status="missing", misses=3, since=date(2026, 11, 18), **kw):
    return mk_link(norm_status=status, live=False, trusted_misses=misses, absent_since=since, **kw)


def test_single_graded_link_without_score_raises_to_stage_3_only():
    assert decide(links=[mk_link(norm_status="graded")]) == (3, None)


def test_single_submitted_link_raises_to_stage_3_only():
    assert decide(links=[mk_link(norm_status="submitted")]) == (3, None)


def test_one_real_score_is_received():
    assert decide(links=[mk_link(norm_status="open", score="9/10")]) == (4, "portal")


@pytest.mark.parametrize("a,b", [("submitted", "submitted"), ("submitted", "graded"), ("graded", "graded")])
def test_two_different_sources_submitted_or_graded_is_received(a, b):
    links = [mk_link(norm_status=a), mk_link(source="powerschool", external_key="p1", norm_status=b)]
    assert decide(links=links) == (4, "portal")


def test_two_links_of_the_same_source_are_not_two_sources():
    links = [mk_link(norm_status="graded"), mk_link(norm_status="graded", external_key="k2")]
    assert decide(links=links) == (3, None)


def test_screenshot_link_does_not_make_the_second_source():
    links = [mk_link(norm_status="graded"),
             mk_link(source="powerschool", external_key="p1", norm_status="graded", via="screenshot")]
    assert decide(links=links) == (3, None)


def test_screenshot_score_does_not_count_for_route_a():
    assert decide(links=[mk_link(norm_status="open", score="95", via="screenshot")])[0] == 1


def test_two_sources_with_a_live_missing_link_is_not_received():
    links = [mk_link(norm_status="graded"), mk_link(source="powerschool", external_key="p1", norm_status="graded"),
             mk_link(source="powerschool", external_key="p2", norm_status="missing")]
    assert decide(links=links)[0] == 1


def test_real_score_with_a_live_missing_link_is_not_received():
    links = [mk_link(norm_status="open", score="9/10"), mk_link(external_key="k2", norm_status="missing")]
    assert decide(links=links)[0] == 1


def test_stale_non_live_links_do_not_make_the_second_source():
    links = [mk_link(norm_status="graded"),
             mk_link(source="powerschool", external_key="p1", norm_status="graded", live=False)]
    assert decide(links=links) == (3, None)


@pytest.mark.parametrize("score", ["95", "7/10", "0"])
def test_real_score_on_live_link_is_received(score):
    assert decide(links=[mk_link(norm_status="open", score=score)]) == (4, "portal")


@pytest.mark.parametrize("score", [None, "", "-", "--", "n/a", "N/A", "missing"])
def test_placeholder_score_is_not_evidence(score):
    assert decide(links=[mk_link(norm_status="open", score=score)])[0] == 1


def test_graded_with_a_live_missing_link_is_not_received():
    links = [mk_link(norm_status="graded"), mk_link(norm_status="missing", external_key="k2")]
    assert decide(links=links)[0] == 1


def test_graded_plus_live_late_of_one_source_is_stage_3_not_received():
    links = [mk_link(norm_status="graded"), mk_link(norm_status="late", external_key="k2")]
    assert decide(links=links) == (3, None)


def test_screenshot_graded_link_is_not_evidence():
    assert decide(links=[mk_link(norm_status="graded", score="95", via="screenshot")])[0] == 1


def test_parent_done_record_with_single_graded_link_stays_stage_3():
    assert decide({"stage": 2}, [mk_link(norm_status="graded")]) == (3, None)


@pytest.mark.parametrize("status", ["missing", "late"])
def test_cleared_link_with_three_misses_over_two_days_never_closes(status):
    assert decide(links=[gone(status)]) == (1, None)


def test_powerschool_only_cleared_record_never_reaches_stage_4():
    link = gone("missing", misses=9, since=date(2026, 11, 1), source="powerschool")
    assert decide(links=[link], today=date(2026, 12, 31)) == (1, None)


def test_two_misses_are_not_enough():
    assert decide(links=[gone(misses=2)])[0] == 1


def test_three_misses_in_one_day_are_not_enough():
    assert decide(links=[gone(since=TODAY)])[0] == 1


def test_three_misses_over_one_day_are_not_enough():
    assert decide(links=[gone(since=date(2026, 11, 19))])[0] == 1


def test_absent_cleared_link_does_not_count_when_a_live_link_is_missing():
    assert decide(links=[gone(), mk_link(norm_status="missing", external_key="k2")])[0] == 1


def test_screenshot_absent_link_never_counts():
    assert decide(links=[gone(via="screenshot")])[0] == 1


def test_absent_link_that_was_only_open_is_not_evidence():
    assert decide(links=[gone("open")])[0] == 1


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_live_submitted_link_raises_stage_to_at_least_three(stage):
    assert decide({"stage": stage}, [mk_link(norm_status="submitted")])[0] == 3


def test_live_submitted_never_lowers_a_stage_4_record():
    st = {"stage": 4, "received_evidence": "teacher_confirmed"}
    assert decide(st, [mk_link(norm_status="submitted")])[0] == 4


@pytest.mark.parametrize("status", ["open", "late"])
def test_open_and_late_links_cast_no_vote(status):
    assert decide({"stage": 2}, [mk_link(norm_status=status)]) == (2, None)


def test_no_links_keeps_manual_stage():
    assert decide({"stage": 2}) == (2, None)


@pytest.mark.parametrize("evidence", ["portal", "migration", "teacher_confirmed"])
def test_stage_4_with_live_missing_link_is_reopened_to_stage_1(evidence):
    st = {"stage": 4, "received_evidence": evidence}
    assert decide(st, [mk_link(norm_status="missing")]) == (1, None)


@pytest.mark.parametrize("evidence", ["migration", "teacher_confirmed"])
def test_absence_never_reopens_migration_or_teacher_confirmed(evidence):
    st = {"stage": 4, "received_evidence": evidence}
    assert decide(st, [gone(misses=1, since=TODAY)]) == (4, evidence)


# ---- review round 1 ----
@pytest.mark.parametrize("status,score", [("graded", None), ("open", "95")])
def test_absent_link_with_graded_or_scored_last_status_is_not_received_by_rule_a(status, score):
    link = mk_link(live=False, norm_status=status, score=score, trusted_misses=1, absent_since=TODAY)
    assert stages.portal_decision(mk_state(stage=1), [link], TODAY) == (1, None)


@pytest.mark.parametrize("kw", [dict(new_status="graded", new_score="95"), dict()])
def test_untrusted_seen_scrape_changes_nothing(kw):
    link = mk_link(live=False, trusted_misses=2, absent_since=date(2026, 11, 18), last_miss_hash="h0")
    assert scrape(link, seen=True, trusted=False, **kw) == link
