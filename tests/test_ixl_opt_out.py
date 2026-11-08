"""Roster flag `"ixl": false`: a child who left IXL is never flagged, in any consumer."""
import json
import logging
from datetime import timedelta

import pytest
from test_watchdog import ny, utc_iso

from school_dashboard import digest, scrape_meta, state as state_mod, watchdog
from school_dashboard.state import update_from_ixl_files

MATH = {"math": {"assigned": 3, "done": 1, "remaining": 2}}
OLD = {"updated": "2026-10-31T06:00:00", "totals": MATH, "remaining": []}
GOOD = {"status": "ok", "errors": [], "totals": MATH, "remaining": []}
NOW = ny(2026, 11, 6, 8, 30)


def _config(tmp_path, monkeypatch, ford):
    cfg = tmp_path / "roster.json"
    cfg.write_text(json.dumps({"children": {
        "Ford": {"grade": "5th", "school": "S", **ford},
        "Jack": {"grade": "5th", "school": "S"}}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.delenv("SOURCE_IXL_ENABLED", raising=False)
    monkeypatch.setattr(state_mod, "_config_cache", None)


@pytest.fixture
def ixl_dir(tmp_path):
    d = tmp_path / "ixl"
    d.mkdir()
    (d / "jack-assigned.json").write_text(json.dumps(GOOD))
    return d


def _state():
    return {"ixl": {"Ford": dict(OLD), "Jack": dict(OLD)}, "schoology": {}}


def test_opted_out_child_without_file_is_not_flagged(tmp_path, monkeypatch, ixl_dir):
    _config(tmp_path, monkeypatch, {"ixl": False})
    state = _state()
    update_from_ixl_files(state, str(ixl_dir))
    assert state["scrape_meta"]["ixl"]["unavailable"] == {}
    assert "Ford" in state["ixl"]  # the old entry may stay


def test_default_and_explicit_true_are_still_flagged(tmp_path, monkeypatch, ixl_dir):
    _config(tmp_path, monkeypatch, {"ixl": True})
    state = _state()
    update_from_ixl_files(state, str(ixl_dir))
    assert "Ford" in state["scrape_meta"]["ixl"]["unavailable"]


@pytest.mark.parametrize("bad", ["false", 0, None, "no"])
def test_non_bool_flag_is_ignored_with_a_warning(tmp_path, monkeypatch, ixl_dir, caplog, bad):
    _config(tmp_path, monkeypatch, {"ixl": bad})
    state = _state()
    with caplog.at_level(logging.WARNING, logger="school_dashboard.state"):
        update_from_ixl_files(state, str(ixl_dir))
    assert "Ford" in state["scrape_meta"]["ixl"]["unavailable"]
    assert "'ixl' must be true or false" in caplog.text
    assert [r["name"] for r in state_mod.get_roster()] == ["Ford", "Jack"]


def test_roster_shape_is_unchanged_by_the_flag(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"ixl": False})
    assert state_mod.get_roster()[0] == {"id": "ford", "name": "Ford", "grade": "5th", "school": "S"}


def _stale_entry(child="Ford", reason="failed"):
    since = utc_iso(NOW - timedelta(hours=48))
    return {"ixl": {"Ford": dict(OLD)}, "schoology": {},
            "scrape_meta": {"ixl": {"unavailable": {child: {"reason": reason, "since": since}}}}}


def test_watchdog_does_not_page_for_an_opted_out_child(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"ixl": False})
    assert watchdog.check_scrape_degraded(_stale_entry(), ["ixl"], NOW) == []


def test_watchdog_still_pages_for_a_child_that_is_not_opted_out(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {})
    got = watchdog.check_scrape_degraded(_stale_entry(), ["ixl"], NOW)
    assert got == [watchdog.Problem("ixl_scrape", "scrape_degraded", 48.0)]


def test_whole_source_marker_is_not_suppressed_by_an_opt_out(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"ixl": False})
    got = watchdog.check_scrape_degraded(_stale_entry("*", "missing"), ["ixl"], NOW)
    assert len(got) == 1


def test_quick_check_and_warnings_ignore_the_opted_out_child(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"ixl": False})
    state = _stale_entry()
    assert not scrape_meta.unavailable_for(state, "ixl", "Ford")
    assert scrape_meta.scrape_warnings(state) == []
    assert scrape_meta.data_warnings_block(state, NOW) == ""
    p = tmp_path / "s.json"
    p.write_text(json.dumps(state))
    text, _ = digest.build_quick_check(str(p), today="2026-11-06")
    assert "IXL unavailable" not in text


def test_opt_out_matches_through_an_alias(tmp_path, monkeypatch):
    cfg = tmp_path / "roster.json"
    cfg.write_text(json.dumps({"children": {"Jack": {"grade": "5th", "school": "S", "ixl": False}}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.setattr(state_mod, "_config_cache", None)
    state = _stale_entry("Jack")
    state["ixl"] = {"Jack": dict(OLD)}
    assert scrape_meta.scrape_warnings(state) == []
