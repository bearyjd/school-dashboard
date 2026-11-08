"""Runs the REAL sync/school-sync.sh (and, through it, gc-refresh.sh / gc-scrape.sh and
`school-state`) against fake `ixl`, `sgy`, `gc`, `powerschool` CLIs on PATH.

No network, no credentials: the stubs replay the synthetic contract fixtures, the state dir
is a tmp dir, and NTFY_TOPIC is unset so a failure alert's curl goes nowhere.
Stub behaviour is steered by FAKE_* env vars; every stub call is appended to calls.log.
"""
import json
import os
import site
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contract_support import load

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "sync" / "school-sync.sh"

STUB = """#!/bin/sh
# fake {name}: log the call (plus the in-flight markers visible at that moment), then replay a fixture.
echo "{name} $*" >> "$FAKE_CALLS"
ls "$SCHOOL_STATE_DIR" 2>/dev/null | grep '^sync.running' | sed 's/^/marker:/' >> "$FAKE_CALLS"
[ "${{FAKE_{upper}_FAIL:-}}" = 1 ] && exit 1
{body}
"""
MAINTENANCE_FLAG = '{"operation_id": "x", "since": "2026-01-01T00:00:00+00:00", "reason": "t"}'
BODIES = {
    "ixl": 'cat "$FAKE_DATA/ixl.json"',
    # FAKE_SGY_MAINTENANCE: switch maintenance on while the sync is already running.
    "sgy": ('[ -n "${FAKE_SGY_MAINTENANCE:-}" ] && echo \'' + MAINTENANCE_FLAG
            + '\' > "$SCHOOL_STATE_DIR/maintenance.json"\ncat "$FAKE_DATA/sgy.json"'),
    "powerschool": 'cat "$FAKE_DATA/powerschool.json"',
    "gc": ('case "$1" in\n  teams) cat "$FAKE_DATA/gc-teams.json" ;;\n'
           '  summary) cat "$FAKE_DATA/gc-summary-$(echo "$*" | sed \'s/.*--team \\([^ ]*\\).*/\\1/\').json" ;;\n'
           '  *) exit 0 ;;\nesac'),
}


class Env:
    def __init__(self, tmp_path: Path):
        self.state = tmp_path / "state"
        self.bin = tmp_path / "bin"
        self.data = tmp_path / "data"
        for d in (self.state, self.bin, self.data, tmp_path / "home" / ".ixl", tmp_path / "ixl"):
            d.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).date()
        for name in ("ixl", "sgy", "powerschool"):
            (self.data / f"{name}.json").write_text(json.dumps(load(name, today)))
        # Shifted fixture timestamps can land in the future (earlier hour today); stamp them "now".
        for name, key in (("sgy", "timestamp"), ("powerschool", "scraped_at")):
            doc = json.loads((self.data / f"{name}.json").read_text())
            doc[key] = datetime.now().isoformat()
            (self.data / f"{name}.json").write_text(json.dumps(doc))
        gc = load("gc", today)
        (self.data / "gc-teams.json").write_text(json.dumps(gc["teams"]))
        for tid, summary in gc["summaries"].items():
            (self.data / f"gc-summary-{tid}.json").write_text(json.dumps(summary))
        for name, body in BODIES.items():
            stub = self.bin / name
            stub.write_text(STUB.format(name=name, upper=name.upper(), body=body))
            stub.chmod(0o755)
        (tmp_path / "home" / ".ixl" / "accounts.env").write_text("alpha:alpha@sample.invalid:pw\n")
        self.calls = tmp_path / "calls.log"
        self.calls.touch()
        self.env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(tmp_path / "home"),
            "PYTHONPATH": str(REPO), "TZ": "UTC", "PYTHONUSERBASE": site.getuserbase(),
            "SCHOOL_DASHBOARD_ENV": "/nonexistent", "SCHOOL_STATE_DIR": str(self.state),
            "IXL_DIR": str(tmp_path / "ixl"), "SGY_FILE": str(tmp_path / "sgy-daily.json"),
            "PS_FILE": str(tmp_path / "ps-daily.json"), "PS_USERNAME": "alpha",
            "GC_TOKEN": "fake-token", "GC_REFRESH_LOCK": str(tmp_path / "refresh.lock"),
            "SYNC_SCRIPT_DIR": str(REPO / "sync"),
            "FAKE_CALLS": str(self.calls), "FAKE_DATA": str(self.data),
        }

    def run(self, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SCRIPT)], env={**self.env, **extra}, cwd=REPO,
                              capture_output=True, text=True, timeout=120)

    def meta(self) -> dict:
        return json.loads((self.state / "sync_meta.json").read_text())

    def results(self) -> dict:
        return {k: v["last_result"] for k, v in self.meta().items()}

    def called(self) -> list[str]:
        return [ln.split()[0] for ln in self.calls.read_text().splitlines() if not ln.startswith("marker:")]

    def state_json(self) -> dict:
        return json.loads((self.state / "school-state.json").read_text())


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


ALL_OK = {"ixl": "ok", "sgy": "ok", "powerschool": "ok", "gc": "ok"}


def test_all_sources_succeed(env):
    r = env.run()
    assert r.returncode == 0, r.stderr
    assert env.results() == ALL_OK
    st = env.state_json()
    assert st["ixl"] and st["schoology"] and st["powerschool"]
    assert (env.state / "school-dashboard.html").exists()
    assert json.loads((env.state / "gc-schedule.json").read_text())["teams"]
    assert "Sync complete." in r.stderr
    assert not list(env.state.glob("sync.running.*"))


def test_in_flight_marker_exists_while_scraping(env):
    env.run()
    assert any(ln.startswith("marker:sync.running.") for ln in env.calls.read_text().splitlines())


def test_one_source_failure_does_not_stop_the_rest(env):
    r = env.run(FAKE_SGY_FAIL="1")
    assert r.returncode == 0, r.stderr
    assert env.results() == {**ALL_OK, "sgy": "error"}
    assert env.called().count("powerschool") == 1 and env.called().count("gc") >= 1
    st = env.state_json()
    assert st["ixl"] and st["powerschool"]  # merge step still ran with the good sources
    assert "Sync complete." in r.stderr


def test_all_sources_failing_still_merges_and_exits_zero(env):
    """Characterisation test: pins existing behaviour (exit 0, merge and "Sync complete." even
    when every scraper failed); this may legitimately change, e.g. to a non-zero exit."""
    r = env.run(FAKE_IXL_FAIL="1", FAKE_SGY_FAIL="1", FAKE_POWERSCHOOL_FAIL="1")
    assert r.returncode == 0, r.stderr
    assert env.results() == {"ixl": "error", "sgy": "error", "powerschool": "error", "gc": "ok"}
    assert (env.state / "school-state.json").exists()  # merge ran over zero inputs
    assert "Sync complete." in r.stderr


def test_gc_scrape_failure_is_recorded_and_non_fatal(env):
    """Characterisation test: pins existing behaviour; the gc stub's bad team list stands in for any gc-scrape.sh failure."""
    (env.data / "gc-teams.json").write_text("not json")
    r = env.run()
    assert r.returncode == 0, r.stderr
    assert env.results()["gc"] == "error"
    assert env.results()["ixl"] == "ok"


def test_gc_skipped_without_credentials(env):
    r = env.run(GC_TOKEN="", GC_EMAIL="")
    assert r.returncode == 0, r.stderr
    assert "gc not configured" in r.stderr
    assert "gc" not in env.meta() and "gc" not in env.called()


def test_disabled_source_is_paused_and_cached_data_kept(env):
    assert env.run().returncode == 0
    first_run = env.meta()["sgy"]["last_run"]
    env.calls.write_text("")
    r = env.run(SOURCE_SGY_ENABLED="off", FAKE_SGY_FAIL="1")
    assert r.returncode == 0, r.stderr
    assert "sgy" not in env.called()
    sgy = env.meta()["sgy"]
    assert sgy["last_result"] == "paused" and sgy["last_run"] == first_run
    assert env.state_json()["schoology"]  # cached Schoology data survives
    assert "ERROR" not in r.stderr


def test_maintenance_active_skips_everything(env):
    (env.state / "maintenance.json").write_text(MAINTENANCE_FLAG)
    r = env.run()
    assert r.returncode == 0, r.stderr
    assert env.called() == []
    assert not (env.state / "school-state.json").exists()
    assert not (env.state / "sync_meta.json").exists()
    assert "Maintenance mode active" in r.stderr
    assert not list(env.state.glob("sync.running.*"))


def test_maintenance_turned_on_mid_sync_skips_the_merge(env):
    r = env.run(FAKE_SGY_MAINTENANCE="1")
    assert r.returncode == 0, r.stderr
    assert "skipping state update" in r.stderr
    assert not (env.state / "school-state.json").exists()
    assert not (env.state / "school-dashboard.html").exists()


def test_missing_gc_refresh_script_aborts_whole_sync(env, tmp_path):
    """Characterisation test of a KNOWN GAP: pins existing behaviour (missing gc-refresh.sh
    kills the sync with exit 127 under set -e, so the merge never runs); may legitimately change."""
    empty = tmp_path / "no-scripts"
    empty.mkdir()
    r = env.run(SYNC_SCRIPT_DIR=str(empty))
    assert r.returncode == 127
    assert not (env.state / "school-state.json").exists()
    assert env.results() == {"ixl": "ok", "sgy": "ok", "powerschool": "ok"}


def test_failed_state_update_is_fatal(env):
    (env.state / "school-state.json").write_text("{ this is not json")
    r = env.run()
    assert r.returncode == 1
    assert "State update failed" in r.stderr
    assert "Sync complete." not in r.stderr
    assert not (env.state / "school-dashboard.html").exists()
    assert (env.state / "school-state.json").read_text() == "{ this is not json"  # not overwritten
    assert env.results()["ixl"] == "ok"  # scrapes were recorded before the merge failed
    assert not list(env.state.glob("sync.running.*"))
