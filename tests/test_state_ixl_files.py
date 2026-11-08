"""update_from_ixl_files must not refresh a child's `updated` from empty/error files."""
import json

import pytest

from school_dashboard import digest, state as state_mod
from school_dashboard.scrape_meta import scrape_warnings, unavailable_for
from school_dashboard.state import update_from_ixl_files

OLD = {"updated": "2026-10-31T06:00:00", "totals": {"math": {"assigned": 3, "done": 1, "remaining": 2}}, "remaining": []}


def _state():
    return {"ixl": {"Ford": dict(OLD)}, "schoology": {}}


def test_error_shaped_file_is_skipped(tmp_path, caplog):
    (tmp_path / "ford-assigned.json").write_text(json.dumps(
        {"status": "error", "errors": [{"code": "auth.login_failed", "stage": "login"}], "remaining": []}))
    state = _state()
    assert update_from_ixl_files(state, str(tmp_path)) == 0
    assert state["ixl"]["Ford"] == OLD
    assert "ford-assigned.json" in caplog.text


MATH = {"math": {"assigned": 3, "done": 1, "remaining": 2}}


def test_empty_and_non_dict_files_are_skipped(tmp_path):
    (tmp_path / "ford-assigned.json").write_text("")
    (tmp_path / "jack-assigned.json").write_text("[]")
    (tmp_path / "penn-assigned.json").write_text("{}")
    state = _state()
    assert update_from_ixl_files(state, str(tmp_path)) == 0
    assert state["ixl"]["Ford"] == OLD
    assert "Jack" not in state["ixl"] and "Penn" not in state["ixl"]


def test_valid_file_is_merged(tmp_path):
    (tmp_path / "ford-assigned.json").write_text(json.dumps(
        {"status": "ok", "errors": [], "totals": MATH, "remaining": []}))
    state = _state()
    assert update_from_ixl_files(state, str(tmp_path)) == 1
    assert state["ixl"]["Ford"]["totals"] == MATH
    assert state["ixl"]["Ford"]["updated"] != OLD["updated"]


# ---- roster child whose IXL file vanished (school-sync.sh deletes a failed child's file) ----

GOOD = {"status": "ok", "errors": [], "totals": MATH, "remaining": []}


@pytest.fixture
def roster(tmp_path, monkeypatch):
    cfg = tmp_path / "roster.json"
    cfg.write_text(json.dumps({"children": {
        n: {"grade": "5th", "school": "S"} for n in ("Ford", "Jack")}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.delenv("SOURCE_IXL_ENABLED", raising=False)
    monkeypatch.setattr(state_mod, "_config_cache", None)
    d = tmp_path / "ixl"
    d.mkdir()
    return d


def test_child_with_earlier_data_but_no_file_is_unavailable(roster):
    (roster / "jack-assigned.json").write_text(json.dumps(GOOD))
    state = _state()  # Ford has earlier data, no ford file now
    assert update_from_ixl_files(state, str(roster)) == 1
    assert state["scrape_meta"]["ixl"]["unavailable"]["Ford"]["reason"] == "missing"
    assert state["ixl"]["Ford"] == OLD
    assert unavailable_for(state, "ixl", "Ford") and not unavailable_for(state, "ixl", "Jack")
    assert "no scrape result was found" in scrape_warnings(state)[0]


def test_child_that_never_had_ixl_data_is_not_flagged(roster):
    (roster / "ford-assigned.json").write_text(json.dumps(GOOD))
    state = _state()  # Jack: on the roster, never had IXL data
    update_from_ixl_files(state, str(roster))
    assert state["scrape_meta"]["ixl"]["unavailable"] == {}


def test_disabled_source_records_nothing(roster, monkeypatch):
    monkeypatch.setenv("SOURCE_IXL_ENABLED", "0")
    (roster / "jack-assigned.json").write_text(json.dumps(GOOD))
    state = _state()
    update_from_ixl_files(state, str(roster))
    assert state["scrape_meta"]["ixl"]["unavailable"] == {}


def test_recovery_clears_it_and_since_is_kept_while_down(roster):
    (roster / "jack-assigned.json").write_text(json.dumps(GOOD))
    state = _state()
    update_from_ixl_files(state, str(roster))
    since = state["scrape_meta"]["ixl"]["unavailable"]["Ford"]["since"]
    update_from_ixl_files(state, str(roster))
    assert state["scrape_meta"]["ixl"]["unavailable"]["Ford"]["since"] == since
    (roster / "ford-assigned.json").write_text(json.dumps(GOOD))
    update_from_ixl_files(state, str(roster))
    assert state["scrape_meta"]["ixl"]["unavailable"] == {}


def test_quick_check_says_ixl_unavailable_for_that_child(roster, tmp_path):
    (roster / "jack-assigned.json").write_text(json.dumps(GOOD))
    state = _state()
    update_from_ixl_files(state, str(roster))
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state))
    text, _ = digest.build_quick_check(str(p), today="2026-11-01")
    ford = next(ln for ln in text.splitlines() if ln.startswith("Ford:"))
    assert "IXL unavailable" in ford
