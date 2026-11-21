from recon.adapter_support import contract, resolve

from school_dashboard.assignments import adapters
from school_dashboard.assignments.adapters.gamechanger import GameChangerAdapter

ADAPTER = GameChangerAdapter()


def test_valid_payload_yields_nothing_and_no_warnings():
    got = ADAPTER.parse(contract("gc"), resolve_kid=resolve)
    assert got.source == "gamechanger"
    assert got.records == () and got.seen_kids == frozenset() and got.trusted_kids == frozenset()
    assert got.warnings == ()


def test_registered():
    assert isinstance(adapters.ADAPTERS["gamechanger"], GameChangerAdapter)
    assert adapters.ADAPTERS["gamechanger"].source == "gamechanger"


def test_malformed_payload_never_raises_and_warns():
    for bad in (None, [], "x", 5):
        got = ADAPTER.parse(bad, resolve_kid=resolve)
        assert got.records == () and got.trusted_kids == frozenset() and got.warnings
