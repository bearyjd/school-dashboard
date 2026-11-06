"""Opt-in live contract tests against the real scrapers.

PYTEST_DONT_REWRITE

Run: LIVE_E2E=1 pytest -m live tests/e2e/live -q
LIVE_E2E=1 means credentials are REQUIRED: a CLI failure fails the test. Only an explicit
"not configured" condition (CLI missing, or its own unset-credentials message) skips.

Privacy rules for this file (scraped data is real family data):
- never put scraped values in an assert expression or message; compute booleans/counts
  first and fail with count-only messages (assertion rewriting is also disabled above);
- conftest.py redacts any failure report for tests marked live.
Everything is written under tmp_path only.
"""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from school_dashboard import state

pytestmark = pytest.mark.live

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ASSIGNMENT_KEYS = ("course", "title", "due", "category", "score")
GC_EVENT_KEYS = ("date", "time", "type", "opponent", "location", "home_away")
PS_NOT_CONFIGURED = re.compile(r"PS_[A-Z_]+.*not set")


def _require_live(cli: str) -> None:
    __tracebackhide__ = True
    if os.environ.get("LIVE_E2E") != "1":
        pytest.skip("live tests are opt-in: set LIVE_E2E=1")
    if shutil.which(cli) is None:
        pytest.skip(f"`{cli}` CLI not on PATH")


def _run_json(args: list, not_configured: "re.Pattern | None" = None) -> object:
    __tracebackhide__ = True
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        if not_configured is not None and not_configured.search(proc.stderr):
            pytest.skip(f"`{args[0]}` is not configured (credentials unset)")
        pytest.fail(f"`{args[0]}` exited {proc.returncode} (stderr withheld)")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        pytest.fail(f"`{args[0]}` stdout is not valid JSON")


@pytest.fixture(scope="module")
def ps_scrape() -> dict:
    _require_live("powerschool")
    data = _run_json(["powerschool", "summary", "--json"], PS_NOT_CONFIGURED)
    if not isinstance(data, dict):
        pytest.fail("top-level is not an object")
    return data


def _check_assignment(where: str, item: object) -> None:
    __tracebackhide__ = True
    if not isinstance(item, dict):
        pytest.fail(f"{where} is not an object")
    n_missing = len([k for k in ASSIGNMENT_KEYS if k not in item])
    if n_missing:
        pytest.fail(f"{where} lacks {n_missing} required key(s)")
    due = item["due"]
    if due is not None and not DATE_RE.match(str(due)[:10]):
        pytest.fail(f"{where}.due is not YYYY-MM-DD")


def test_powerschool_contract(ps_scrape):
    n_warnings = len(ps_scrape.get("warnings") or [])
    if n_warnings:
        pytest.fail(f"{n_warnings} top-level warning(s)")
    if "scraped_at" not in ps_scrape:
        pytest.fail("scraped_at missing")
    children = ps_scrape.get("children")
    if not isinstance(children, list) or not children:
        pytest.fail("no children")
    for ci, child in enumerate(children):
        for key in ("missing", "late", "upcoming"):
            items = child.get(key) if isinstance(child, dict) else None
            if not isinstance(items, list):
                pytest.fail(f"child[{ci}].{key} is not a list")
            for ii, item in enumerate(items):
                _check_assignment(f"child[{ci}].{key}[{ii}]", item)


def test_powerschool_feeds_state_merge(ps_scrape, tmp_path, monkeypatch):
    roster_path = os.environ.get("LIVE_ROSTER_JSON")
    if not roster_path:
        pytest.skip("set LIVE_ROSTER_JSON to a roster config to run the merge check")
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", roster_path)
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path))
    state._config_cache = None
    try:
        scrape = tmp_path / "ps.json"
        scrape.write_text(json.dumps({**ps_scrape, "scraped_at": datetime.now().isoformat()}))
        s = {"ixl": {}, "schoology": {}, "powerschool": {}, "action_items": []}
        merged = state.update_from_ps_file(s, str(scrape), now=datetime.now() + timedelta(seconds=1))
        warnings = s["powerschool_meta"]["warnings"]
        n_rejected = len([w for w in warnings if "not applied" in w])
        n_orphans = len([w for w in warnings if "not in roster" in w])
        n_skipped = len([w for w in warnings if "ambiguous" in w or "kept previous" in w])
        n_children = len(ps_scrape["children"])
        if n_rejected:
            pytest.fail("scrape rejected by trust rules")
        if n_orphans:
            pytest.fail(f"{n_orphans} child(ren) missing from roster/aliases")
        if merged != n_children - n_skipped:
            pytest.fail(f"merged {merged} of {n_children} children ({n_skipped} skipped)")
        by_first = {state.canonical_name(str(c.get("name") or "").split(" ")[0]): c
                    for c in ps_scrape["children"]}
        n_mismatch = 0
        for name, entry in s["powerschool"].items():
            src = by_first.get(name)
            if src is None:
                n_mismatch += 1
                continue
            n_mismatch += len([k for k in ("missing", "late", "upcoming") if len(entry[k]) != len(src[k])])
        if n_mismatch:
            pytest.fail(f"{n_mismatch} merged list count mismatch(es)")
    finally:
        state._config_cache = None


def _gc_configured() -> bool:
    return bool(os.environ.get("GC_TOKEN") or os.environ.get("GC_EMAIL")
                or (Path.home() / ".gc" / ".env").exists())


def test_gc_contract():
    _require_live("gc")
    if not _gc_configured():
        pytest.skip("gc is not configured (no GC_TOKEN/GC_EMAIL or ~/.gc/.env)")
    teams = _run_json(["gc", "teams", "--json"])
    if not isinstance(teams, list):
        pytest.fail("gc teams --json is not a list")
    if not teams:
        pytest.skip("gc returned no teams")
    for ti, team in enumerate(teams):
        if not (isinstance(team, dict) and team.get("id")):
            pytest.fail(f"team[{ti}] has no id")
    summary = _run_json(["gc", "summary", "--json", "--team", str(teams[0]["id"])])
    schedule = summary.get("schedule") if isinstance(summary, dict) else None
    if not isinstance(schedule, list):
        pytest.fail("summary.schedule is not a list")
    for ei, evt in enumerate(schedule):
        if not isinstance(evt, dict):
            pytest.fail(f"schedule[{ei}] is not an object")
        n_missing = len([k for k in GC_EVENT_KEYS if k not in evt])
        if n_missing:
            pytest.fail(f"schedule[{ei}] lacks {n_missing} required key(s)")
        if not DATE_RE.match(str(evt["date"])[:10]):
            pytest.fail(f"schedule[{ei}].date is not YYYY-MM-DD")
