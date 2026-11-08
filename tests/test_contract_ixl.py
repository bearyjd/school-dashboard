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
from contract_support import build_state, consumers, load, mutate, renamed, set_roster, without

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


def _ixl_meta(built: dict) -> dict:
    return built["state"]["scrape_meta"]["ixl"]


def _renamed_inner(key: str) -> dict:
    return mutate(load("ixl"), lambda d: d.update(
        totals={s: renamed(t, key, "left") for s, t in d["totals"].items()}))


def test_inner_remaining_key_renamed_is_unavailable_not_all_done(env):
    """G1 (guarded): `remaining` -> `left` inside totals must not read as "all done"."""
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, ixl=_renamed_inner("remaining"))
    assert "Alpha" not in built["state"]["ixl"]
    assert _ixl_meta(built)["unavailable"] == {"Alpha": "totals incomplete"}
    assert _ixl_meta(built)["warnings"] == ["IXL Alpha: totals incomplete; no previous data"]
    out = consumers(built)
    assert "Alpha: IXL unavailable" in out["quick"]
    assert "IXL all done" not in out["quick"].split("\n")[0]


def test_bad_payload_keeps_previous_ixl_data_and_clears_on_recovery(env):
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    built = build_state(state_env, tmp_path, ixl=_renamed_inner("remaining"))
    assert built["state"]["ixl"]["Alpha"]["totals"]["Math"]["remaining"] == 3
    assert _ixl_meta(built)["warnings"] == ["IXL Alpha: totals incomplete; previous data kept"]
    assert "Alpha: IXL unavailable" in consumers(built)["quick"]
    healed = build_state(state_env, tmp_path)
    assert _ixl_meta(healed) == {"unavailable": {}, "warnings": []}
    assert "Alpha: IXL 4 remaining (Math, ELA)" in consumers(healed)["quick"]


def test_totals_missing_but_remaining_list_present_is_unavailable(env):
    """G2 (guarded): a payload with only the `remaining` list is not accepted as "all done"."""
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, ixl=without(load("ixl"), "totals"))
    assert "Alpha" not in built["state"]["ixl"]
    assert _ixl_meta(built)["unavailable"] == {"Alpha": "totals missing"}
    assert "Alpha: IXL unavailable" in consumers(built)["quick"]


def test_empty_totals_dict_is_a_legitimate_nothing_assigned(env):
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, ixl={**load("ixl"), "totals": {}, "remaining": []})
    assert built["state"]["ixl"]["Alpha"]["totals"] == {}
    assert _ixl_meta(built)["unavailable"] == {}
    assert "Alpha: IXL all done" in consumers(built)["quick"]


def test_summary_text_survives_totals_lacking_done(env):
    """G3 (guarded): the update refuses the payload, and summary_text no longer KeyErrors on a
    state that already holds incomplete totals."""
    state_env, tmp_path = env
    bad = mutate(load("ixl"), lambda d: d.update(
        totals={s: without(t, "done") for s, t in d["totals"].items()}))
    built = build_state(state_env, tmp_path, ixl=bad)
    assert _ixl_meta(built)["unavailable"] == {"Alpha": "totals incomplete"}
    assert "IXL: unavailable" in state.summary_text(built["state"])
    legacy = {"action_items": [], "children": {"Alpha": {"grade": "5th", "school": "S"}},
              "ixl": {"Alpha": {"totals": {"Math": {"remaining": 2}}}}}
    assert "IXL Math: ?/? done, 2 remaining" in state.summary_text(legacy)
