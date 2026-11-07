"""Contract: `ixl assigned --json` output (tests/fixtures/contracts/ixl.json) vs school_dashboard.

Consumers and the keys they read:
  state.ixl_payload_ok        status, errors, totals | remaining
  state.update_ixl            totals, remaining
  state.summary_text          totals.<subject>.{assigned, done, remaining}
  digest.build_quick_check    totals.<subject>.remaining
  readiness.get_checklist     totals.<subject>.remaining
  overview_items._ixl_rows    totals.<subject>.remaining
"""
import logging

import pytest
from contract_support import (assert_unguarded, build_state, consumers, load, mutate, renamed, set_roster,
                              unguarded_raises, without)

from school_dashboard import state

TOTALS_KEYS = ("assigned", "done", "remaining")


@pytest.fixture
def env(state_env, tmp_path, monkeypatch):
    set_roster(tmp_path, monkeypatch)
    yield state_env, tmp_path
    state._config_cache = None


def test_fixture_has_the_keys_consumers_read():
    data = load("ixl")
    assert data["status"] == "ok" and data["errors"] == []
    assert isinstance(data["remaining"], list)
    assert data["totals"], "totals must be non-empty"
    for subject, totals in data["totals"].items():
        for key in TOTALS_KEYS:
            assert isinstance(totals[key], int), f"totals[{subject}][{key}]"
    assert state.ixl_payload_ok(data)


def test_fixture_is_accepted_end_to_end(env):
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path)
    ixl = built["state"]["ixl"]
    assert list(ixl) == ["Alpha"]
    assert ixl["Alpha"]["totals"]["Math"]["remaining"] == 3
    assert len(ixl["Alpha"]["remaining"]) == 4

    out = consumers(built)
    assert "Alpha: IXL 4 remaining (Math, ELA)" in out["quick"]
    assert [i["title"] for i in out["items"] if i["source"] == "ixl"] == ["IXL: 4 remaining"]
    ixl_items = [i for i in out["readiness"]["Alpha"] if i["type"] == "ixl"]
    assert {i["detail"]: i["label"] for i in ixl_items} == {
        "Math": "3 IXL skills remaining", "ELA": "1 IXL skill remaining"}
    assert "IXL Math: 1/4 done, 3 remaining" in state.summary_text(built["state"])


@pytest.mark.parametrize("mutation", [
    pytest.param(lambda d: renamed(renamed(d, "totals", "summary"), "remaining", "left"), id="top-level-renamed"),
    pytest.param(lambda d: {**d, "status": "error"}, id="status-error"),
    pytest.param(lambda d: {**d, "errors": [{"message": "x"}]}, id="errors-present"),
])
def test_guarded_bad_payload_is_skipped_with_a_warning(env, caplog, mutation):
    state_env, tmp_path = env
    with caplog.at_level(logging.WARNING, logger="school_dashboard.state"):
        built = build_state(state_env, tmp_path, ixl=mutation(load("ixl")))
    assert built["state"]["ixl"] == {}
    assert "empty or error-shaped IXL payload" in caplog.text


def test_silent_degradation_inner_remaining_key_renamed(env):
    """UNGUARDED: `remaining` -> `left` inside totals passes ixl_payload_ok, then every
    consumer reads 0 remaining. Quick check says "IXL all done"; no warning anywhere."""
    state_env, tmp_path = env
    bad = mutate(load("ixl"), lambda d: d.update(
        totals={s: renamed(t, "remaining", "left") for s, t in d["totals"].items()}))
    built = build_state(state_env, tmp_path, ixl=bad)
    assert "Alpha" in built["state"]["ixl"]
    out = consumers(built)
    assert_unguarded("Alpha: IXL all done" in out["quick"], "G1")
    assert_unguarded(not [i for i in out["items"] if i["source"] == "ixl"], "G1")
    assert_unguarded(not [i for i in out["readiness"].get("Alpha", []) if i["type"] == "ixl"], "G1")


def test_silent_degradation_totals_missing_but_remaining_list_present(env):
    """UNGUARDED: ixl_payload_ok accepts `totals` OR `remaining`; with only the list the
    child is stored with empty totals and reads as "all done"."""
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, ixl=without(load("ixl"), "totals"))
    assert_unguarded(built["state"]["ixl"]["Alpha"]["totals"] == {}, "G2")
    assert_unguarded("Alpha: IXL all done" in consumers(built)["quick"], "G2")


def test_summary_text_crashes_when_totals_lack_done(env):
    """UNGUARDED: summary_text indexes t['done'] / t['assigned'] directly -> KeyError."""
    state_env, tmp_path = env
    bad = mutate(load("ixl"), lambda d: d.update(
        totals={s: without(t, "done") for s, t in d["totals"].items()}))
    built = build_state(state_env, tmp_path, ixl=bad)
    with unguarded_raises(KeyError, "G3"):
        state.summary_text(built["state"])
