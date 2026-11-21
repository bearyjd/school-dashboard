from copy import deepcopy

import pytest
from recon.adapter_support import contract, no_leaks, resolve

from school_dashboard import state
from school_dashboard.assignments.adapters.ixl import IxlAdapter
from school_dashboard.assignments.model import SourceRecord

ADAPTER = IxlAdapter()


def _payload(totals=None, **extra) -> dict:
    return {"status": "ok", "warnings": [], "errors": [],
            "totals": totals if totals is not None else {}, "remaining": [], **extra}


def _subject(assigned=4, done=1, remaining=3) -> dict:
    return {"assigned": assigned, "done": done, "in_progress": 0, "not_started": 0, "remaining": remaining}


def test_contract_fixture_exact_records():
    data = contract("ixl")
    got = ADAPTER.parse({"alpha": data}, resolve_kid=resolve)

    def rec(subject):
        return SourceRecord(
            source="ixl", kid="Alex", class_name=None, title=f"IXL {subject}", due_date=None,
            portal_status="open", raw_status=f"{data['totals'][subject]['remaining']} remaining",
            score=None, link=None, external_key=f"ixl:{subject.casefold()}",
            raw=data["totals"][subject])

    assert got.records == (rec("Math"), rec("ELA"))
    assert got.records[0].raw_status == "3 remaining" and got.records[1].raw_status == "1 remaining"
    assert got.source == "ixl"
    assert got.seen_kids == got.trusted_kids == frozenset({"Alex"})
    assert got.warnings == ()


def test_each_kid_resolved_and_isolated():
    data = {"alpha": _payload({"Math": _subject()}), "beta": _payload({"ELA": _subject(2, 0, 2)})}
    got = ADAPTER.parse(data, resolve_kid=resolve)
    assert {(r.kid, r.title) for r in got.records} == {("Alex", "IXL Math"), ("Sam", "IXL ELA")}
    assert got.trusted_kids == frozenset({"Alex", "Sam"})


def test_resolve_kid_receives_the_key():
    seen = []

    def spy(name: str) -> str:
        seen.append(name)
        return "Zed"

    got = ADAPTER.parse({"alpha": _payload({"Math": _subject()})}, resolve_kid=spy)
    assert seen == ["alpha"] and got.records[0].kid == "Zed"


def test_subject_with_nothing_assigned_has_no_record():
    got = ADAPTER.parse({"alpha": _payload({"Math": _subject(0, 0, 0)})}, resolve_kid=resolve)
    assert got.records == () and got.trusted_kids == frozenset({"Alex"})


def test_all_done_subject_stays_open_with_zero_remaining():
    got = ADAPTER.parse({"alpha": _payload({"Math": _subject(3, 3, 0)})}, resolve_kid=resolve)
    assert [(r.portal_status, r.raw_status) for r in got.records] == [("open", "0 remaining")]


def test_empty_totals_is_trusted_with_no_records():
    got = ADAPTER.parse({"alpha": _payload({})}, resolve_kid=resolve)
    assert got.records == () and got.trusted_kids == frozenset({"Alex"})


@pytest.mark.parametrize("bad", [
    None, 5, "x", [], {}, {"status": "error", "totals": {}}, {"totals": {}, "errors": ["boom"]},
    {"remaining": []}, {"totals": "x"}, {"totals": None},
    {"totals": {"Math": 5}}, {"totals": {"Math": {"assigned": 1, "done": 0}}},
    {"totals": {"Math": {"assigned": "1", "done": 0, "remaining": 1}}},
    {"totals": {"Math": {"assigned": True, "done": 0, "remaining": 1}}},
])
def test_malformed_child_payload_is_untrusted_without_records(bad):
    got = ADAPTER.parse({"alpha": bad}, resolve_kid=resolve)
    assert got.records == () and got.trusted_kids == frozenset()
    assert got.seen_kids == frozenset({"Alex"}) and got.warnings
    no_leaks(got.warnings)


def test_trust_agrees_with_v1_payload_checks():
    for bad in ({"totals": {"Math": {"assigned": 1}}}, {"remaining": []}, _payload({"Math": _subject()}),
                {"status": "error", "totals": {}}):
        v1 = state.ixl_payload_ok(bad) and state.ixl_totals_problem(bad) is None
        got = ADAPTER.parse({"alpha": bad}, resolve_kid=resolve)
        assert (frozenset({"Alex"}) == got.trusted_kids) is v1


@pytest.mark.parametrize("payload", [None, [], "x", 5])
def test_malformed_top_level_never_raises(payload):
    got = ADAPTER.parse(payload, resolve_kid=resolve)
    assert got.records == () and got.seen_kids == frozenset() and got.warnings


def test_non_string_kid_key_is_skipped_with_warning():
    got = ADAPTER.parse({5: _payload({"Math": _subject()}), "": _payload({})}, resolve_kid=resolve)
    assert got.records == () and got.seen_kids == frozenset() and got.warnings


def test_input_is_not_mutated():
    data = {"alpha": contract("ixl")}
    before = deepcopy(data)
    ADAPTER.parse(data, resolve_kid=resolve)
    assert data == before


def _sam(name: str) -> str:
    return "Sam" if name.strip().casefold() in ("samantha", "sam") else name.title()


def test_long_and_short_name_files_for_one_child_merge_deterministically():
    older = _payload({"Math": _subject(4, 1, 3), "ELA": _subject(2, 1, 1)})
    newer = _payload({"math": _subject(5, 4, 1)})
    got = ADAPTER.parse({"samantha": older, "sam": newer}, resolve_kid=_sam)  # later entry = more recent file
    by_key = {r.external_key: r for r in got.records}
    assert len(got.records) == len(by_key) == 2 and {r.kid for r in got.records} == {"Sam"}
    assert by_key["ixl:math"].raw_status == "1 remaining" and by_key["ixl:ela"].raw_status == "1 remaining"
    assert got.trusted_kids == frozenset({"Sam"})
    assert any("1 child entries sharing a name were merged" in w for w in got.warnings)
    no_leaks(got.warnings)


def test_a_malformed_duplicate_does_not_distrust_the_child_while_a_good_one_exists():
    got = ADAPTER.parse({"samantha": _payload({"Math": _subject()}), "sam": {"status": "error"}}, resolve_kid=_sam)
    assert got.trusted_kids == frozenset({"Sam"}) and len(got.records) == 1


def test_all_duplicates_malformed_leaves_the_child_untrusted():
    got = ADAPTER.parse({"samantha": {"status": "error"}, "sam": {"status": "error"}}, resolve_kid=_sam)
    assert got.trusted_kids == frozenset() and got.seen_kids == frozenset({"Sam"})
