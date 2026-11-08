"""Contract: `ixl assigned --json` output (tests/fixtures/contracts/ixl.json) vs school_dashboard.

Consumers and the keys they read:
  state.ixl_payload_ok        status, errors, totals | remaining
  state.update_ixl            totals, remaining
  state.summary_text          totals.<subject>.{assigned, done, remaining}
  digest.build_quick_check    totals.<subject>.remaining
  readiness.get_checklist     totals.<subject>.remaining
  overview_items._ixl_rows    totals.<subject>.remaining
"""
import json
import logging
from datetime import datetime, timezone

import pytest
from contract_support import (build_state, consumers, line, load, mutate, reasons, renamed, run_update,
                              set_roster, warnings_of, without)

from school_dashboard import scrape_meta, state

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


def _renamed_inner(key: str) -> dict:
    return mutate(load("ixl"), lambda d: d.update(
        totals={s: renamed(t, key, "left") for s, t in d["totals"].items()}))


def test_inner_remaining_key_renamed_is_unavailable_not_all_done(env):
    """G1 (guarded): `remaining` -> `left` inside totals must not read as "all done"."""
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, ixl=_renamed_inner("remaining"))
    assert "Alpha" not in built["state"]["ixl"]
    assert reasons(built, "ixl") == {"Alpha": "unexpected"}
    assert warnings_of(built, "IXL") == [
        "IXL Alpha: the last scrape returned unexpected data; no earlier data to show"]
    assert line(consumers(built)["quick"], "Alpha") == "Alpha: IXL unavailable, SGY 4 open"


def test_bad_payload_keeps_previous_ixl_data_and_clears_on_recovery(env):
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    built = build_state(state_env, tmp_path, ixl=_renamed_inner("remaining"))
    assert built["state"]["ixl"]["Alpha"]["totals"]["Math"]["remaining"] == 3
    assert warnings_of(built, "IXL") == [
        "IXL Alpha: the last scrape returned unexpected data; showing the last good data from 2026-11-05"]
    assert line(consumers(built)["quick"], "Alpha").startswith("Alpha: IXL unavailable")
    healed = build_state(state_env, tmp_path)
    assert reasons(healed, "ixl") == {} and warnings_of(healed, "IXL") == []
    assert "Alpha: IXL 4 remaining (Math, ELA)" in consumers(healed)["quick"]


def test_totals_missing_but_remaining_list_present_is_unavailable(env):
    """G2 (guarded): a payload with only the `remaining` list is not accepted as "all done"."""
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, ixl=without(load("ixl"), "totals"))
    assert "Alpha" not in built["state"]["ixl"]
    assert reasons(built, "ixl") == {"Alpha": "unexpected"}
    assert line(consumers(built)["quick"], "Alpha").startswith("Alpha: IXL unavailable")


def test_empty_totals_dict_on_first_run_is_nothing_assigned(env):
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, ixl={**load("ixl"), "totals": {}, "remaining": []})
    assert built["state"]["ixl"]["Alpha"]["totals"] == {}
    assert reasons(built, "ixl") == {}
    assert line(consumers(built)["quick"], "Alpha").startswith("Alpha: IXL all done")


def test_empty_totals_after_real_data_is_unavailable(env):
    """M1: an empty result right after a non-empty one is suspicious, like an empty Schoology scrape."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    built = build_state(state_env, tmp_path, ixl={**load("ixl"), "totals": {}, "remaining": []})
    assert built["state"]["ixl"]["Alpha"]["totals"]["Math"]["remaining"] == 3
    assert reasons(built, "ixl") == {"Alpha": "empty"}
    assert warnings_of(built, "IXL") == [
        "IXL Alpha: the last scrape returned nothing; showing the last good data from 2026-11-05"]


def test_summary_text_survives_totals_lacking_done(env):
    """G3 (guarded): the update refuses the payload, and summary_text no longer KeyErrors on a
    state that already holds incomplete totals."""
    state_env, tmp_path = env
    bad = mutate(load("ixl"), lambda d: d.update(
        totals={s: without(t, "done") for s, t in d["totals"].items()}))
    built = build_state(state_env, tmp_path, ixl=bad)
    assert reasons(built, "ixl") == {"Alpha": "unexpected"}
    assert "IXL: unavailable" in state.summary_text(built["state"])
    legacy = {"action_items": [], "children": {"Alpha": {"grade": "5th", "school": "S"}},
              "ixl": {"Alpha": {"totals": {"Math": {"remaining": 2}}}}}
    assert "IXL Math: ?/? done, 2 remaining" in state.summary_text(legacy)


# Error-shaped output of `ixl assigned --json` (flat envelope from render_json_result:
# status/warnings/errors, with the payload keys spread at the top level).
_ERROR_ENVELOPE = {"status": "error", "warnings": [], "errors": [
    {"stage": "auth", "code": "login_failed", "message": "Login failed"}]}


@pytest.mark.parametrize("payload, expected", [
    pytest.param(_ERROR_ENVELOPE, "failed", id="status-error"),
    pytest.param({**_ERROR_ENVELOPE, "status": "warning", "totals": {}, "remaining": []}, "failed",
                 id="errors-with-warning-status"),
    pytest.param({"status": "ok", "warnings": [], "errors": []}, "unexpected", id="no-totals-no-remaining"),
    pytest.param("not-json{", "unexpected", id="invalid-json"),
    pytest.param(["a", "list"], "unexpected", id="non-dict"),
])
def test_every_skipped_file_is_recorded_as_unavailable(env, payload, expected):
    """H1: auth/Cloudflare failures and garbled files are visible, keep previous data, and clear."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    ixl_file = tmp_path / "inputs" / "ixl" / "alpha-assigned.json"
    ixl_file.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    built = run_update(state_env, tmp_path)
    assert built["state"]["ixl"]["Alpha"]["totals"]["Math"]["remaining"] == 3
    assert reasons(built, "ixl") == {"Alpha": expected}
    assert len(warnings_of(built, "IXL")) == 1
    assert line(consumers(built)["quick"], "Alpha").startswith("Alpha: IXL unavailable")
    ixl_file.write_text(json.dumps(load("ixl")))
    assert reasons(run_update(state_env, tmp_path), "ixl") == {}


def test_unreadable_file_is_recorded_as_failed(env):
    state_env, tmp_path = env
    d = tmp_path / "inputs" / "ixl"
    d.mkdir(parents=True)
    (d / "alpha-assigned.json").mkdir()  # a directory: read_text raises OSError
    built = run_update(state_env, tmp_path, ixl_only=True)
    assert reasons(built, "ixl") == {"Alpha": "failed"}


def test_missing_ixl_dir_or_no_files_is_a_whole_source_problem(env):
    """M3: nothing to read at all is recorded as `*`, and recovery clears it."""
    state_env, tmp_path = env
    empty = tmp_path / "inputs" / "ixl"
    empty.mkdir(parents=True)
    built = run_update(state_env, tmp_path, ixl_only=True)
    assert reasons(built, "ixl") == {"*": "missing"}
    assert warnings_of(built, "IXL") == ["IXL: no scrape result was found; no earlier data to show"]
    assert reasons(build_state(state_env, tmp_path), "ixl") == {}


def test_since_is_preserved_while_unavailable_and_dropped_on_recovery(env, monkeypatch):
    state_env, tmp_path = env
    clock = {"now": datetime(2026, 11, 5, 12, tzinfo=timezone.utc)}
    monkeypatch.setattr(scrape_meta, "_utc_now", lambda: clock["now"])
    bad = _renamed_inner("remaining")
    first = build_state(state_env, tmp_path, ixl=bad)
    clock["now"] = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    second = build_state(state_env, tmp_path, ixl=bad)
    for built in (first, second):
        assert built["state"]["scrape_meta"]["ixl"]["unavailable"]["Alpha"]["since"] == "2026-11-05T12:00:00+00:00"
    healed = build_state(state_env, tmp_path)
    assert healed["state"]["scrape_meta"]["ixl"]["unavailable"] == {}
    clock["now"] = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
    again = build_state(state_env, tmp_path, ixl=bad)
    assert again["state"]["scrape_meta"]["ixl"]["unavailable"]["Alpha"]["since"] == "2026-11-07T12:00:00+00:00"


def test_warning_shows_how_long_it_has_lasted(env):
    st = {"ixl": {}, "scrape_meta": {"ixl": {"unavailable": {
        "Alpha": {"reason": "failed", "since": "2026-11-05T12:00:00+00:00"}}}}}
    now = datetime(2026, 11, 8, 13, 0, tzinfo=timezone.utc)
    assert scrape_meta.scrape_warnings(st, now) == [
        "IXL Alpha: the last scrape failed; no earlier data to show (unavailable for 3 days)"]
    assert scrape_meta.scrape_warnings(st) == ["IXL Alpha: the last scrape failed; no earlier data to show"]


def test_disabled_source_never_records_unavailable(env, monkeypatch):
    state_env, tmp_path = env
    monkeypatch.setenv("SOURCE_IXL_ENABLED", "0")
    (tmp_path / "inputs" / "ixl").mkdir(parents=True)
    assert reasons(run_update(state_env, tmp_path, ixl_only=True), "ixl") == {}
