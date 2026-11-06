"""Opt-in live contract tests against the real scrapers.

Run: LIVE_E2E=1 pytest -m live tests/e2e/live -q
Skipped unless LIVE_E2E=1 and the CLI is on PATH. Assertion messages use indices,
never child names or assignment titles. Everything is written under tmp_path only.
"""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest

from school_dashboard import state

pytestmark = pytest.mark.live

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ASSIGNMENT_KEYS = ("course", "title", "due", "category", "score")
GC_EVENT_KEYS = ("date", "time", "type", "opponent", "location", "home_away")


def _require_live(cli: str) -> None:
    if os.environ.get("LIVE_E2E") != "1":
        pytest.skip("live tests are opt-in: set LIVE_E2E=1")
    if shutil.which(cli) is None:
        pytest.skip(f"`{cli}` CLI not on PATH")


def _run_json(args: list, skip_markers: tuple = ("not set", "token", "auth", "credential")) -> object:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        err = proc.stderr.lower()
        if any(m in err for m in skip_markers):
            pytest.skip(f"`{args[0]}` has no usable credentials (exit {proc.returncode})")
        pytest.fail(f"`{' '.join(args)}` exited {proc.returncode}")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        pytest.fail(f"`{args[0]}` stdout is not valid JSON")


@pytest.fixture(scope="module")
def ps_scrape() -> dict:
    _require_live("powerschool")
    data = _run_json(["powerschool", "summary", "--json"])
    assert isinstance(data, dict), "top-level is not an object"
    return data


def test_powerschool_contract(ps_scrape):
    assert not ps_scrape.get("warnings"), f"{len(ps_scrape['warnings'])} top-level warning(s)"
    assert "scraped_at" in ps_scrape
    children = ps_scrape.get("children")
    assert isinstance(children, list) and children, "no children"
    for ci, child in enumerate(children):
        for key in ("missing", "late", "upcoming"):
            items = child.get(key)
            assert isinstance(items, list), f"child[{ci}].{key} is not a list"
            for ii, item in enumerate(items):
                where = f"child[{ci}].{key}[{ii}]"
                assert isinstance(item, dict), f"{where} is not an object"
                missing_keys = [k for k in ASSIGNMENT_KEYS if k not in item]
                assert not missing_keys, f"{where} lacks keys {missing_keys}"
                assert item["due"] is None or DATE_RE.match(str(item["due"])[:10]), f"{where}.due format"


def test_powerschool_feeds_state_merge(ps_scrape, tmp_path, monkeypatch):
    roster_path = os.environ.get("LIVE_ROSTER_JSON")
    if roster_path:
        monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", roster_path)
    else:
        monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path))
    state._config_cache = None
    try:
        scrape = tmp_path / "ps.json"
        scrape.write_text(json.dumps({**ps_scrape, "scraped_at": datetime.now().isoformat()}))
        s = {"ixl": {}, "schoology": {}, "powerschool": {}, "action_items": []}
        merged = state.update_from_ps_file(s, str(scrape), now=datetime.now() + timedelta(seconds=1))
        warnings = s["powerschool_meta"]["warnings"]
        assert not [w for w in warnings if "not applied" in w], "scrape rejected by trust rules"
        assert not [w for w in warnings if "not in roster" in w], "child(ren) missing from roster/aliases"
        expected = len(ps_scrape["children"]) - len([w for w in warnings if "ambiguous" in w or "kept previous" in w])
        assert merged == expected, f"merged {merged} of {len(ps_scrape['children'])} children"
        by_first = {state.canonical_name(c["name"].split()[0]): c for c in ps_scrape["children"] if c.get("name")}
        for name, entry in s["powerschool"].items():
            src = by_first[name]
            for key in ("missing", "late", "upcoming"):
                assert len(entry[key]) == len(src[key]), f"{key} count mismatch for merged child"
    finally:
        state._config_cache = None


def test_gc_contract():
    _require_live("gc")
    teams = _run_json(["gc", "teams", "--json"])
    assert isinstance(teams, list), "gc teams --json is not a list"
    if not teams:
        pytest.skip("gc returned no teams")
    for ti, team in enumerate(teams):
        assert team.get("id"), f"team[{ti}] has no id"
    summary = _run_json(["gc", "summary", "--json", "--team", str(teams[0]["id"])])
    assert isinstance(summary, dict) and isinstance(summary.get("schedule"), list), "summary.schedule missing"
    for ei, evt in enumerate(summary["schedule"]):
        assert isinstance(evt, dict), f"schedule[{ei}] is not an object"
        missing_keys = [k for k in GC_EVENT_KEYS if k not in evt]
        assert not missing_keys, f"schedule[{ei}] lacks keys {missing_keys}"
        assert DATE_RE.match(str(evt["date"])[:10]), f"schedule[{ei}].date format"
