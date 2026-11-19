"""merge.py: match() scenarios from fixtures and combine_portal_status()."""
import pytest

from recon.support import mk_link, mk_record, mk_state, scenario_params
from school_dashboard.assignments import merge


@pytest.mark.parametrize("sc", scenario_params("merge"))
def test_match_returns_expected_kind(sc):
    cands = [mk_state(**{**c, "id": c["id"]}) for c in sc["candidates"]]
    index = {(s, k, e): i for s, k, e, i in sc["link_index"]}
    res = merge.match(mk_record(**sc["record"]), cands, index, sc["aliases"])
    assert res.kind == sc["expect"]["kind"]


@pytest.mark.parametrize("sc", scenario_params("merge"))
def test_match_returns_expected_assignment_id(sc):
    cands = [mk_state(**c) for c in sc["candidates"]]
    index = {(s, k, e): i for s, k, e, i in sc["link_index"]}
    res = merge.match(mk_record(**sc["record"]), cands, index, sc["aliases"])
    assert res.assignment_id == sc["expect"]["assignment_id"]


@pytest.mark.parametrize("sc", [p for p in scenario_params("merge") if "candidate_ids" in p.values[0]["expect"]])
def test_ambiguous_match_lists_candidates_without_merging(sc):
    cands = [mk_state(**c) for c in sc["candidates"]]
    res = merge.match(mk_record(**sc["record"]), cands, {}, sc["aliases"])
    assert sorted(res.candidate_ids) == sc["expect"]["candidate_ids"]


def test_match_does_not_mutate_candidates():
    cands = [mk_state(id=1, due_date="2026-11-20", title="Quiz", title_key="quiz")]
    before = list(cands)
    merge.match(mk_record(title="Quiz", due_date="2026-11-20"), cands, {})
    assert cands == before


def L(status, live=True, **kw):
    return mk_link(norm_status=status, live=live, **kw)


@pytest.mark.parametrize("links,expected", [
    ([], ("none", False)),
    ([L("graded")], ("graded", False)),
    ([L("submitted"), L("graded", external_key="k2")], ("submitted", False)),
    ([L("open"), L("graded", external_key="k2")], ("open", False)),
    ([L("late"), L("open", external_key="k2")], ("late", False)),
    ([L("missing"), L("late", external_key="k2")], ("missing", False)),
    ([L("late"), L("graded", external_key="k2")], ("late", False)),
    ([L("submitted"), L("missing", external_key="k2")], ("missing", True)),
    ([L("graded"), L("missing", external_key="k2")], ("missing", True)),
], ids=lambda v: None)
def test_combine_portal_status_worst_case_and_conflict(links, expected):
    assert merge.combine_portal_status(links) == expected


def test_combine_late_never_creates_conflict():
    assert merge.combine_portal_status([L("graded"), L("late", external_key="k2")])[1] is False


@pytest.mark.parametrize("was", ["missing", "late"])
def test_combine_absent_missing_or_late_link_is_cleared(was):
    assert merge.combine_portal_status([L(was, live=False, trusted_misses=1)]) == ("cleared", False)


def test_combine_absent_links_do_not_vote_against_live_ones():
    assert merge.combine_portal_status([L("missing", live=False), L("graded", external_key="k2")]) == ("graded", False)


def test_combine_absent_missing_with_live_open_is_open():
    assert merge.combine_portal_status([L("missing", live=False), L("open", external_key="k2")]) == ("open", False)


def test_combine_absent_missing_plus_absent_graded_is_cleared():
    links = [L("missing", live=False), L("graded", live=False, external_key="k2")]
    assert merge.combine_portal_status(links) == ("cleared", False)


def test_combine_screenshot_link_votes_for_display_status():
    assert merge.combine_portal_status([L("graded", via="screenshot")]) == ("graded", False)
