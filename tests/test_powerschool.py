"""PowerSchool source: state merge (trust rules), digest data, quick check."""
import copy
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from school_dashboard import digest, powerschool, state

FIXTURE = Path(__file__).parent / "fixtures" / "powerschool-summary.json"
NOW = datetime(2026, 11, 5, 8, 0, 0)


@pytest.fixture(autouse=True)
def reset_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "none.json"))
    state._config_cache = None
    yield
    state._config_cache = None


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def _write(tmp_path, data, name="ps.json") -> str:
    p = tmp_path / name
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return str(p)


def _fresh_state() -> dict:
    return {"ixl": {}, "schoology": {}, "powerschool": {}, "action_items": []}


def _merge(s: dict, path: str, now: datetime = NOW) -> int:
    return state.update_from_ps_file(s, path, now=now)


def test_empty_state_has_powerschool_section():
    assert state._empty_state()["powerschool"] == {}


def test_merge_trusted_scrape_keyed_by_canonical_name(tmp_path):
    s = _fresh_state()
    assert _merge(s, _write(tmp_path, _fixture())) == 3
    assert set(s["powerschool"]) == {"Alex", "Jack", "Jo"}  # John -> Jack via alias
    alex = s["powerschool"]["Alex"]
    assert alex["missing"][0]["title"] == "Quiz 4 Corrections"
    assert alex["late"][0]["course"] == "English"
    assert alex["upcoming"][0]["due"] == "2026-11-07"
    assert s["powerschool"]["Jack"]["upcoming"][0]["title"] == "Map Test"


def test_config_alias_is_reused(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"children": {}, "name_aliases": {"alex": "Alexander"}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    s = _fresh_state()
    _merge(s, _write(tmp_path, _fixture()))
    assert "Alexander" in s["powerschool"]


def test_trusted_scrape_replaces_child_atomically(tmp_path):
    s = _fresh_state()
    _merge(s, _write(tmp_path, _fixture()))
    data = _fixture()
    data["children"][0]["missing"] = []
    data["children"][0]["late"] = []
    _merge(s, _write(tmp_path, data, "2.json"))
    assert s["powerschool"]["Alex"]["missing"] == []
    assert s["powerschool"]["Alex"]["late"] == []


def test_merge_does_not_touch_other_sections(tmp_path):
    s = _fresh_state()
    s["ixl"] = {"Alex": {"totals": {}}}
    before = copy.deepcopy(s["ixl"])
    _merge(s, _write(tmp_path, _fixture()))
    assert s["ixl"] == before


@pytest.mark.parametrize("kind", ["warnings", "malformed", "empty", "notdict", "stale_scraped_at", "stale_mtime"])
def test_untrusted_scrape_keeps_previous_state(tmp_path, kind):
    s = _fresh_state()
    _merge(s, _write(tmp_path, _fixture()))
    previous = copy.deepcopy(s["powerschool"])

    bad = _fixture()
    bad["children"][0]["missing"] = []
    if kind == "warnings":
        bad["warnings"] = ["login failed"]
        path = _write(tmp_path, bad, "bad.json")
    elif kind == "malformed":
        path = _write(tmp_path, "{not json", "bad.json")
    elif kind == "empty":
        path = _write(tmp_path, {"scraped_at": "2026-11-05T06:00:00", "children": [], "warnings": []}, "bad.json")
    elif kind == "notdict":
        path = _write(tmp_path, [1, 2], "bad.json")
    elif kind == "stale_scraped_at":
        bad["scraped_at"] = "2026-11-01T06:00:00"
        path = _write(tmp_path, bad, "bad.json")
    else:
        del bad["scraped_at"]
        path = _write(tmp_path, bad, "bad.json")
        old = NOW.timestamp() - 72 * 3600
        os.utime(path, (old, old))

    assert _merge(s, path) == 0
    assert s["powerschool"] == previous
    assert s["powerschool_meta"]["warnings"]


def test_missing_file_keeps_previous_and_warns(tmp_path):
    s = _fresh_state()
    _merge(s, _write(tmp_path, _fixture()))
    previous = copy.deepcopy(s["powerschool"])
    assert _merge(s, str(tmp_path / "absent.json")) == 0
    assert s["powerschool"] == previous
    assert s["powerschool_meta"]["warnings"]


def test_missing_file_without_prior_data_is_silent(tmp_path):
    s = _fresh_state()
    assert _merge(s, str(tmp_path / "absent.json")) == 0
    assert not s.get("powerschool_meta", {}).get("warnings")


def test_child_level_warning_keeps_only_that_child(tmp_path):
    s = _fresh_state()
    _merge(s, _write(tmp_path, _fixture()))
    data = _fixture()
    data["children"][0]["warnings"] = ["grades page timed out"]
    data["children"][0]["missing"] = []
    data["children"][1]["upcoming"] = []
    assert _merge(s, _write(tmp_path, data, "p.json")) == 2
    assert s["powerschool"]["Alex"]["missing"]  # kept
    assert s["powerschool"]["Jack"]["upcoming"] == []  # replaced
    assert any("Alex" in w for w in s["powerschool_meta"]["warnings"])


def test_good_scrape_clears_warnings(tmp_path):
    s = _fresh_state()
    s["powerschool_meta"] = {"warnings": ["old"]}
    _merge(s, _write(tmp_path, _fixture()))
    assert s["powerschool_meta"]["warnings"] == []


def test_format_block_lists_missing_late_upcoming(tmp_path):
    s = _fresh_state()
    _merge(s, _write(tmp_path, _fixture()))
    block = powerschool.format_block(s)
    assert "Alex" in block
    assert "MISSING (1)" in block and "Quiz 4 Corrections" in block
    assert "LATE (1)" in block and "Essay Draft" in block
    assert "DUE SOON (1)" in block and "Lab Report" in block
    assert "Jo: nothing missing" in block


def test_format_block_empty_state():
    assert powerschool.format_block({}) == ""


def _state_file(tmp_path) -> str:
    s = _fresh_state()
    _merge(s, _write(tmp_path, _fixture()))
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps(s))
    return str(sp)


def test_quick_check_includes_powerschool(tmp_path):
    text, _ = digest.build_quick_check(_state_file(tmp_path))
    assert "Alex" in text
    assert "missing 1" in text and "Quiz 4 Corrections" in text
    assert "late 1" in text and "Essay Draft" in text
    assert "due soon 1" in text and "Lab Report" in text


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    def fake(prompt, *a, **k):
        captured["p"] = prompt
        return "ok"

    monkeypatch.setattr(digest, "_call_litellm", fake)
    return captured


def test_morning_prompt_contains_powerschool_block(tmp_path, monkeypatch):
    captured = _capture(monkeypatch)
    digest.build_morning_digest(
        state_path=_state_file(tmp_path), db_path=str(tmp_path / "x.db"),
        facts_path=str(tmp_path / "f.json"), gcal_events=[], litellm_url="u", api_key="k",
        model="m", today="2026-11-05")
    assert "MISSING (1)" in captured["p"] and "Quiz 4 Corrections" in captured["p"]


def test_afternoon_prompt_contains_powerschool_block(tmp_path, monkeypatch):
    captured = _capture(monkeypatch)
    digest.build_afternoon_digest(
        state_path=_state_file(tmp_path), litellm_url="u", api_key="k", model="m",
        today="2026-11-05")
    assert "DUE SOON (1)" in captured["p"] and "Lab Report" in captured["p"]


def test_prompt_unchanged_without_powerschool(tmp_path, monkeypatch):
    captured = _capture(monkeypatch)
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps({"ixl": {}, "schoology": {}, "action_items": []}))
    digest.build_afternoon_digest(
        state_path=str(sp), litellm_url="u", api_key="k", model="m", today="2026-11-05")
    assert "PowerSchool" not in captured["p"]
