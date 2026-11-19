from copy import deepcopy
from datetime import date

import pytest
from recon.adapter_support import contract, hkey, no_leaks, resolve

from school_dashboard.assignments.adapters.powerschool import PowerSchoolAdapter
from school_dashboard.assignments.model import SourceRecord

ADAPTER = PowerSchoolAdapter()


def _item(title: str = "Task", course: str | None = "Sample Algebra", due: str | None = "2026-11-06",
          score: object = None) -> dict:
    return {"course": course, "title": title, "due": due, "category": None, "score": score}


def _payload(missing=(), late=(), upcoming=(), name="Alpha Tester", **extra) -> dict:
    entry = {"name": name, "missing": list(missing), "late": list(late),
             "upcoming": list(upcoming), "warnings": [], **extra}
    return {"children": [entry], "warnings": []}


def _statuses(payload) -> list[str]:
    return [r.portal_status for r in ADAPTER.parse(payload, resolve_kid=resolve).records]


def test_contract_fixture_exact_records():
    data = contract("powerschool")
    got = ADAPTER.parse(data, resolve_kid=resolve)
    a, b = data["children"][0], data["children"][1]

    def rec(raw, status, raw_status, kid="Alex", score=None):
        return SourceRecord(
            source="powerschool", kid=kid, class_name=raw["course"], title=raw["title"],
            due_date=date.fromisoformat(raw["due"]), portal_status=status, raw_status=raw_status,
            score=score, link=None,
            external_key=hkey(raw["course"].casefold(), raw["title"].casefold(), raw["due"]), raw=raw)

    assert got.records == (
        rec(a["missing"][0], "missing", "missing"),
        rec(a["late"][0], "graded", "late", score="7/10"),
        rec(a["upcoming"][0], "open", "upcoming"),
        rec(b["upcoming"][0], "open", "upcoming", kid="Sam"),
    )
    assert got.source == "powerschool"
    assert got.seen_kids == got.trusted_kids == frozenset({"Alex", "Sam", "Jo"})
    assert got.warnings == ()


@pytest.mark.parametrize("lists,expected", [
    ({"missing": [_item()]}, "missing"), ({"late": [_item()]}, "late"),
    ({"upcoming": [_item()]}, "open"),
    ({"missing": [_item(score="missing")]}, "missing"),
    ({"missing": [_item(score="-")]}, "missing"),
    ({"late": [_item(score="")]}, "late"),
    ({"late": [_item(score="7/10")]}, "graded"),
    ({"missing": [_item(score="95")]}, "graded"),
    ({"upcoming": [_item(score="A")]}, "graded"),
    ({"upcoming": [_item(score=88)]}, "graded"),
])
def test_status_mapping(lists, expected):
    assert _statuses(_payload(**lists)) == [expected]


def test_score_is_kept_as_text():
    rec = ADAPTER.parse(_payload(late=[_item(score=88)]), resolve_kid=resolve).records[0]
    assert rec.score == "88"
    none = ADAPTER.parse(_payload(late=[_item()]), resolve_kid=resolve).records[0]
    assert none.score is None and none.link is None


def test_key_stable_when_row_moves_between_lists():
    def key(lists: tuple[list, list, list]) -> str:
        return ADAPTER.parse(_payload(*lists), resolve_kid=resolve).records[0].external_key

    row = _item()
    keys = {key(([row], [], [])), key(([], [row], [])), key(([], [], [row]))}
    assert len(keys) == 1


def test_key_changes_with_due_title_or_class():
    base = ADAPTER.parse(_payload(missing=[_item()]), resolve_kid=resolve).records[0].external_key
    for other in (_item(due="2026-11-07"), _item(title="Other"), _item(course="Sample Art")):
        got = ADAPTER.parse(_payload(missing=[other]), resolve_kid=resolve).records[0].external_key
        assert got != base


def test_empty_course_and_due_are_none():
    rec = ADAPTER.parse(_payload(upcoming=[_item(course=None, due=None)]), resolve_kid=resolve).records[0]
    assert rec.class_name is None and rec.due_date is None
    assert rec.external_key == hkey("", "task", "")


def test_resolve_kid_alias_and_last_first_name_form():
    got = ADAPTER.parse(_payload(upcoming=[_item()], name="Tester, Alpha"), resolve_kid=resolve)
    assert got.seen_kids == frozenset({"Alex"})


@pytest.mark.parametrize("payload", [None, [], "x", 5, {}, {"children": "x"}, {"children": None}])
def test_malformed_payload_never_raises(payload):
    got = ADAPTER.parse(payload, resolve_kid=resolve)
    assert got.records == () and got.trusted_kids == frozenset() and got.seen_kids == frozenset()
    assert got.warnings


@pytest.mark.parametrize("entry", [None, 5, "x", [], {}, {"name": ""}, {"name": 5}])
def test_unusable_child_entry_skipped_with_warning(entry):
    data = _payload(upcoming=[_item()])
    data["children"].insert(0, entry)
    got = ADAPTER.parse(data, resolve_kid=resolve)
    assert got.trusted_kids == frozenset({"Alex"}) and len(got.records) == 1 and got.warnings
    no_leaks(got.warnings)


@pytest.mark.parametrize("broken", [
    {"missing": "x"}, {"late": None}, {"upcoming": {"a": 1}},
])
def test_non_list_lists_leave_kid_untrusted_without_records(broken):
    data = _payload(missing=[_item()], late=[_item(title="B")], upcoming=[_item(title="C")])
    data["children"][0].update(broken)
    got = ADAPTER.parse(data, resolve_kid=resolve)
    assert got.records == () and got.seen_kids == frozenset({"Alex"})
    assert got.trusted_kids == frozenset() and got.warnings
    no_leaks(got.warnings)


def test_missing_list_key_is_untrusted():
    data = _payload(missing=[_item()])
    del data["children"][0]["upcoming"]
    got = ADAPTER.parse(data, resolve_kid=resolve)
    assert got.trusted_kids == frozenset()


@pytest.mark.parametrize("bad_item", [5, "x", {"course": "c", "due": None},
                                      {"title": 5, "due": None}, {"title": "T", "course": "c"}])
def test_malformed_item_leaves_kid_untrusted(bad_item):
    got = ADAPTER.parse(_payload(missing=[bad_item]), resolve_kid=resolve)
    assert got.trusted_kids == frozenset() and got.seen_kids == frozenset({"Alex"})
    assert got.warnings
    no_leaks(got.warnings)


def test_blank_title_item_makes_kid_untrusted():
    got = ADAPTER.parse(_payload(missing=[_item(title=" ")]), resolve_kid=resolve)
    assert got.records == () and got.trusted_kids == frozenset()


def test_per_child_warnings_make_that_kid_untrusted_only():
    data = _payload(upcoming=[_item()])
    data["children"].append({"name": "Beta Tester", "missing": [], "late": [],
                             "upcoming": [_item(title="B")], "warnings": ["x"]})
    got = ADAPTER.parse(data, resolve_kid=resolve)
    assert got.trusted_kids == frozenset({"Alex"}) and got.seen_kids == frozenset({"Alex", "Sam"})
    assert len(got.records) == 2 and got.warnings
    no_leaks(got.warnings)


@pytest.mark.parametrize("warnings", [["scraper hiccup"], "boom"])
def test_top_level_warnings_make_every_kid_untrusted(warnings):
    data = _payload(upcoming=[_item()])
    data["warnings"] = warnings
    got = ADAPTER.parse(data, resolve_kid=resolve)
    assert got.trusted_kids == frozenset() and len(got.records) == 1 and got.warnings
    no_leaks(got.warnings)


def test_file_ok_false_makes_every_kid_untrusted():
    got = ADAPTER.parse(_payload(upcoming=[_item()]), resolve_kid=resolve, file_ok=False)
    assert got.trusted_kids == frozenset() and got.seen_kids == frozenset({"Alex"}) and got.warnings
    ok = ADAPTER.parse(_payload(upcoming=[_item()]), resolve_kid=resolve, file_ok=True)
    assert ok.trusted_kids == frozenset({"Alex"})


def test_duplicate_kid_entries_untrusted_wins():
    data = _payload(upcoming=[_item()])
    data["children"].append({**data["children"][0], "warnings": ["x"]})
    got = ADAPTER.parse(data, resolve_kid=resolve)
    assert got.trusted_kids == frozenset()


def test_unparseable_due_keeps_row_and_warns():
    got = ADAPTER.parse(_payload(upcoming=[_item(due="soonish")]), resolve_kid=resolve)
    assert got.records[0].due_date is None and any("due" in w for w in got.warnings)
    assert got.trusted_kids == frozenset({"Alex"})


def test_input_is_not_mutated():
    data = contract("powerschool")
    before = deepcopy(data)
    ADAPTER.parse(data, resolve_kid=resolve)
    assert data == before
