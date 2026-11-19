from copy import deepcopy
from datetime import date

import pytest
from recon.adapter_support import contract, hkey, no_leaks, resolve

from school_dashboard import state
from school_dashboard.assignments import adapters
from school_dashboard.assignments.adapters.schoology import SchoologyAdapter
from school_dashboard.assignments.model import SourceRecord

ADAPTER = SchoologyAdapter()


def _rec(title, course, due, status, link, key, raw, *, kid="Alex", raw_status=None):
    return SourceRecord(
        source="schoology", kid=kid, class_name=course or None, title=title,
        due_date=due, portal_status=status, raw_status=raw_status if raw_status is not None else raw["status"],
        score=None, link=link, external_key=key, raw=raw)


def _row(title="Task", course="Sample Math", due="2026-11-06", status="unknown", link="") -> dict:
    return {"title": title, "course": course, "due_date": due, "status": status, "link": link}


def _payload(*rows, name="Alpha Tester", **entry_extra) -> dict:
    entry = {"child": {"name": name}, "scrape_confidence": "high", "assignments": list(rows),
             "warnings": [], **entry_extra}
    return {"per_child": [entry]}


def test_registry_and_source():
    assert adapters.ADAPTERS["schoology"].source == "schoology"
    assert set(adapters.ADAPTERS) == {"schoology", "powerschool", "ixl", "gamechanger"}


def test_contract_fixture_exact_records():
    data = contract("sgy")
    got = ADAPTER.parse(data, resolve_kid=resolve)
    rows = data["per_child"][0]["assignments"]
    expected = (
        _rec("Synthetic Worksheet 1", "Sample Math", date(2026, 11, 6), "open", None,
             "/assignment/900001", rows[0]),
        _rec("Sample Reading Log", "Sample ELA", date(2026, 11, 9), "submitted", None,
             "/assignment/900002", rows[1]),
        _rec("Practice Set A", "Sample Science", date(2026, 11, 12), "open", None,
             hkey("sample science", "practice set a", "2026-11-12"), rows[2]),
        _rec("Old Quiz Corrections", "Sample Math", date(2026, 11, 2), "missing", None,
             "/assignment/900004", rows[3]),
        _rec("Undated Project", "", None, "open", None, hkey("", "undated project", ""), rows[4]),
        _rec("Beta Graded Task", "Sample Art", date(2026, 11, 6), "graded", None,
             "/assignment/900101", data["per_child"][1]["assignments"][0], kid="Sam"),
    )
    assert got.records == expected
    assert got.source == "schoology"
    assert got.seen_kids == frozenset({"Alex", "Sam", "Jo"})
    assert got.trusted_kids == frozenset({"Alex"})
    assert got.warnings and isinstance(got.warnings, tuple)
    no_leaks(got.warnings)


def test_resolve_kid_receives_first_name_word_and_alias_applies():
    seen: list[str] = []

    def spy(name: str) -> str:
        seen.append(name)
        return "Alexander"

    got = ADAPTER.parse(_payload(_row(), name="Alpha Tester"), resolve_kid=spy)
    assert seen == ["Alpha"]
    assert got.seen_kids == frozenset({"Alexander"}) and got.records[0].kid == "Alexander"


@pytest.mark.parametrize("raw,expected,warns", [
    ("submitted", "submitted", False), ("Turned In", "submitted", False),
    ("complete", "submitted", False), ("COMPLETED", "submitted", False),
    ("graded", "graded", False), ("Missing", "missing", False), ("late", "late", False),
    ("unknown", "open", False), ("", "open", False), ("  ", "open", False),
    ("banana", "open", True), (5, "open", True), (None, "open", False),
])
def test_status_mapping(raw, expected, warns):
    got = ADAPTER.parse(_payload(_row(status=raw)), resolve_kid=resolve)
    assert [r.portal_status for r in got.records] == [expected]
    assert got.trusted_kids == frozenset({"Alex"})
    assert any("unrecognised status" in w for w in got.warnings) is warns
    no_leaks(got.warnings)


def test_unknown_status_row_is_kept_with_raw_status():
    got = ADAPTER.parse(_payload(_row(status="banana")), resolve_kid=resolve)
    assert got.records[0].raw_status == "banana"


@pytest.mark.parametrize("payload", [None, [], "x", 5, {}, {"per_child": "x"}, {"per_child": None}])
def test_malformed_payload_never_raises(payload):
    got = ADAPTER.parse(payload, resolve_kid=resolve)
    assert got.records == () and got.trusted_kids == frozenset() and got.seen_kids == frozenset()
    assert got.warnings


@pytest.mark.parametrize("entry", [
    None, 5, "x", [], {}, {"child": None}, {"child": {"name": 5}}, {"child": {"name": "  "}},
])
def test_unusable_child_entry_is_skipped_with_warning(entry):
    got = ADAPTER.parse({"per_child": [entry, _payload(_row())["per_child"][0]]}, resolve_kid=resolve)
    assert got.trusted_kids == frozenset({"Alex"}) and len(got.records) == 1
    assert got.warnings
    no_leaks(got.warnings)


@pytest.mark.parametrize("bad_assignments", [
    "nope", {"a": 1}, [5], [{"course": "c", "due_date": ""}], [{"title": 5, "due_date": ""}],
    [{"title": "T", "course": "c"}],
])
def test_malformed_assignments_leave_kid_untrusted_without_records(bad_assignments):
    payload = _payload(name="Alpha Tester")
    payload["per_child"][0]["assignments"] = bad_assignments
    got = ADAPTER.parse(payload, resolve_kid=resolve)
    assert got.records == () and got.seen_kids == frozenset({"Alex"})
    assert got.trusted_kids == frozenset() and got.warnings
    no_leaks(got.warnings)


def test_trust_rule_agrees_with_v1_shape_check():
    cases = [_payload(_row()), _payload("nope"), _payload(_row(), _row(title="B")),
             {"per_child": [{"child": {"name": "Alpha"}, "assignments": [{"title": "T"}]}]},
             {"per_child": [{"child": {"name": "Alpha"}, "assignments": "x"}]}]
    cases.extend(contract("sgy") for _ in range(1))
    for payload in cases:
        got = ADAPTER.parse(payload, resolve_kid=resolve)
        for entry in payload["per_child"]:
            conf = str(entry.get("scrape_confidence", "high")).casefold()
            if conf in ("partial", "failed"):
                continue
            kid = resolve(entry["child"]["name"].split()[0])
            assert (state.sgy_assignments_problem(entry) is None) == (kid in got.trusted_kids)


@pytest.mark.parametrize("confidence,trusted", [
    ("high", True), ("medium", True), (None, True), ("partial", False), ("PARTIAL", False),
    ("failed", False), (7, False),
])
def test_confidence_controls_trust_but_rows_are_kept(confidence, trusted):
    payload = _payload(_row())
    if confidence is None:
        del payload["per_child"][0]["scrape_confidence"]
    else:
        payload["per_child"][0]["scrape_confidence"] = confidence
    got = ADAPTER.parse(payload, resolve_kid=resolve)
    assert len(got.records) == 1
    assert (got.trusted_kids == frozenset({"Alex"})) is trusted
    assert got.seen_kids == frozenset({"Alex"})


@pytest.mark.parametrize("warnings", [["calendar_feed: error"], "boom", ["x", "y"]])
def test_top_level_warnings_make_every_kid_untrusted(warnings):
    payload = _payload(_row())
    payload["warnings"] = warnings
    got = ADAPTER.parse(payload, resolve_kid=resolve)
    assert got.trusted_kids == frozenset() and len(got.records) == 1 and got.warnings
    no_leaks(got.warnings)


def test_external_key_stable_when_retitled_with_link():
    a = ADAPTER.parse(_payload(_row(title="Old", link="https://sgy.example.invalid/assignment/1")),
                      resolve_kid=resolve).records[0]
    b = ADAPTER.parse(_payload(_row(title="New", link="https://sgy.example.invalid/assignment/1")),
                      resolve_kid=resolve).records[0]
    assert a.external_key == b.external_key == "https://sgy.example.invalid/assignment/1"
    assert a.link == "https://sgy.example.invalid/assignment/1"


def test_relative_path_link_is_a_key_but_not_a_link():
    rec = ADAPTER.parse(_payload(_row(link="/assignment/77")), resolve_kid=resolve).records[0]
    assert rec.link is None and rec.external_key == "/assignment/77"


@pytest.mark.parametrize("link", ["javascript:alert(1)", "ftp://x.invalid/a", "#", "/", "  ", None, 5])
def test_unusable_link_is_dropped_and_key_is_hash(link):
    rec = ADAPTER.parse(_payload(_row(link=link, title="Hw", course="Sample Math", due="2026-11-06")),
                        resolve_kid=resolve).records[0]
    assert rec.link is None
    assert rec.external_key == hkey("sample math", "hw", "2026-11-06")


def test_hash_key_ignores_case_and_punctuation_but_not_due():
    a = ADAPTER.parse(_payload(_row(title="Hw 1!")), resolve_kid=resolve).records[0]
    b = ADAPTER.parse(_payload(_row(title="hw 1")), resolve_kid=resolve).records[0]
    c = ADAPTER.parse(_payload(_row(title="hw 1", due="2026-11-07")), resolve_kid=resolve).records[0]
    assert a.external_key == b.external_key != c.external_key


def test_unparseable_due_keeps_row_with_no_date_and_warns():
    got = ADAPTER.parse(_payload(_row(due="someday")), resolve_kid=resolve)
    assert got.records[0].due_date is None and got.warnings
    assert any("due" in w for w in got.warnings)


def test_blank_title_row_is_skipped_with_warning():
    got = ADAPTER.parse(_payload(_row(title="  "), _row(title="Real")), resolve_kid=resolve)
    assert [r.title for r in got.records] == ["Real"] and got.warnings


def test_duplicate_kid_entries_untrusted_wins():
    good = _payload(_row())["per_child"][0]
    bad = deepcopy(good)
    bad["scrape_confidence"] = "partial"
    got = ADAPTER.parse({"per_child": [good, bad]}, resolve_kid=resolve)
    assert got.trusted_kids == frozenset()


def test_throwing_resolver_counts_as_unusable_entry():
    def boom(name: str) -> str:
        raise RuntimeError(name)

    got = ADAPTER.parse(_payload(_row()), resolve_kid=boom)
    assert got.records == () and got.seen_kids == frozenset() and got.warnings
    no_leaks(got.warnings)


def test_input_is_not_mutated():
    data = contract("sgy")
    before = deepcopy(data)
    ADAPTER.parse(data, resolve_kid=resolve)
    assert data == before
