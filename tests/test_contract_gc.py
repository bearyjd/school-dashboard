"""Contract: `gc teams --json` / `gc summary --json --team ID` (tests/fixtures/contracts/gc.json).

The REAL sync/gc-scrape.sh runs against a stub `gc` that replays the fixture, then the
resulting gc-schedule.json is read by the real consumers.

Consumers and the keys they read:
  sync/gc-scrape.sh                teams[].{id, name}; summary.{schedule, team_name}
  overview_events.gc_events        schedule[].{date, time, type, opponent, location}
  digest._load_gc_events           schedule[].{date, time, type, opponent, location, home_away}
"""
import json
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest
from contract_support import TODAY, assert_unguarded, load, mutate, renamed

from school_dashboard import digest
from school_dashboard.overview_events import gc_events

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "sync" / "gc-scrape.sh"
ROSTER = ["Alpha", "Beta", "Gamma"]
EVENT_KEYS = ("date", "time", "type", "opponent", "location", "home_away")

STUB = """#!/usr/bin/env python3
import json, os, sys
fx = json.load(open(os.environ["GC_CONTRACT_FIXTURE"]))
a = sys.argv[1:]
if a[0] == "teams":
    print(json.dumps(fx["teams"]))
elif a[0] == "summary":
    print(json.dumps(fx["summaries"][a[a.index("--team") + 1]]))
else:
    sys.exit("unexpected gc subcommand: " + " ".join(a))
"""


def _run_scrape(tmp_path: Path, fixture: dict):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    # gc-scrape.sh runs `school-state transition maintenance check`: exit 10 means "maintenance
    # inactive, proceed" (any other code is treated as active and skips the scrape).
    (bindir / "school-state").write_text("#!/bin/sh\nexit 10\n")
    (bindir / "gc").write_text(STUB)
    for tool in bindir.iterdir():
        tool.chmod(0o755)
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps(fixture))
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path),
        "SCHOOL_DASHBOARD_ENV": "/nonexistent", "SCHOOL_STATE_DIR": str(state),
        "SCHOOL_GC_PATH": str(state / "gc-schedule.json"),
        "GC_TEAM_MAP": "team-0001:Alpha,team-0002:Beta", "GC_CONTRACT_FIXTURE": str(fx),
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    out = state / "gc-schedule.json"
    return result, (json.loads(out.read_text()) if out.exists() else None)


@pytest.fixture
def scraped(tmp_path):
    result, data = _run_scrape(tmp_path, load("gc"))
    assert result.returncode == 0, result.stderr
    return data, tmp_path / "state" / "gc-schedule.json"


def test_fixture_has_the_keys_consumers_read():
    data = load("gc")
    for team in data["teams"]:
        assert isinstance(team["id"], str) and isinstance(team["name"], str)
        summary = data["summaries"][team["id"]]
        assert isinstance(summary["schedule"], list) and summary["schedule"]
        for evt in summary["schedule"]:
            for key in EVENT_KEYS:
                assert isinstance(evt[key], str), key
            assert date.fromisoformat(evt["date"])


def test_fixture_is_accepted_end_to_end(scraped):
    data, path = scraped
    assert [(t["team_id"], t["team_name"], t["child"], t["schedule_source"]) for t in data["teams"]] == [
        ("team-0001", "Sample Team Alpha", "Alpha", "api"), ("team-0002", "Sample Team Beta", "Beta", "api")]
    assert [len(t["schedule"]) for t in data["teams"]] == [2, 1]

    events = gc_events(data, ROSTER, TODAY, 14)
    assert [(e["kids"], e["title"], e["time"], e["type"]) for e in events] == [
        (["Alpha"], "vs Sample Opponent One", "09:00", "game"),
        (["Beta"], "vs Sample Opponent Two", "13:00", "game"),
        (["Alpha"], "practice", "17:30", "practice")]
    assert events[0]["location"].startswith("Sample Field")

    lines = [digest._format_gc_event_line(e, TODAY) for e in digest._load_gc_events(str(path), 14, TODAY.isoformat())]
    assert len(lines) == 3
    assert any("vs. Sample Opponent Two, Away, 13:00, @ Sample Pitch" in line for line in lines)


def test_guarded_teams_id_renamed_fails_the_scrape(tmp_path):
    bad = mutate(load("gc"), lambda d: d.update(teams=[renamed(t, "id", "teamId") for t in d["teams"]]))
    result, data = _run_scrape(tmp_path, bad)
    assert result.returncode != 0 and data is None
    log = (tmp_path / "state" / "gc-scrape.log").read_text()
    assert "gc teams failed" in log or "No teams found" in log


def test_guarded_schedule_key_renamed_everywhere_refuses_to_overwrite(tmp_path):
    bad = mutate(load("gc"), lambda d: d.update(summaries={
        k: renamed(v, "schedule", "events") for k, v in d["summaries"].items()}))
    result, data = _run_scrape(tmp_path, bad)
    assert result.returncode != 0 and data is None
    assert "Refusing to overwrite" in (tmp_path / "state" / "gc-scrape.log").read_text()


def test_silent_degradation_schedule_renamed_for_one_team(tmp_path):
    """UNGUARDED: if only some teams lose `schedule` the script exits 0 and writes those
    teams with an empty schedule (only an all-teams-empty result is treated as a failure)."""
    bad = mutate(load("gc"), lambda d: d["summaries"].update(
        {"team-0002": renamed(d["summaries"]["team-0002"], "schedule", "events")}))
    result, data = _run_scrape(tmp_path, bad)
    assert result.returncode == 0
    assert_unguarded([len(t["schedule"]) for t in data["teams"]] == [2, 0], "G13")
    assert_unguarded("WARN" not in (tmp_path / "state" / "gc-scrape.log").read_text(), "G13")


def _scraped_with(tmp_path: Path, edit) -> tuple:
    result, data = _run_scrape(tmp_path, mutate(load("gc"), edit))
    assert result.returncode == 0, result.stderr
    return data, tmp_path / "state" / "gc-schedule.json"


def _rename_event_key(old: str, new: str):
    def edit(d: dict) -> None:
        for summary in d["summaries"].values():
            summary["schedule"] = [renamed(e, old, new) for e in summary["schedule"]]
    return edit


def test_silent_degradation_event_date_renamed_drops_every_event(tmp_path):
    """UNGUARDED: `date` renamed -> both consumers silently see no events."""
    data, path = _scraped_with(tmp_path, _rename_event_key("date", "start_date"))
    assert_unguarded(gc_events(data, ROSTER, TODAY, 14) == [], "G14a")
    assert_unguarded(digest._load_gc_events(str(path), 14, TODAY.isoformat()) == [], "G14a")


def test_silent_degradation_event_time_renamed_blanks_times(tmp_path):
    """UNGUARDED: `time` renamed -> events stay but with no time of day."""
    data, path = _scraped_with(tmp_path, _rename_event_key("time", "start_time"))
    assert_unguarded({e["time"] for e in gc_events(data, ROSTER, TODAY, 14)} == {""}, "G14b")
    assert_unguarded({e["time"] for e in digest._load_gc_events(str(path), 14, TODAY.isoformat())} == {""}, "G14b")


def test_silent_degradation_event_type_renamed_loses_opponent_in_digest(tmp_path):
    """UNGUARDED: the digest only prints the opponent for type == 'game'."""
    data, path = _scraped_with(tmp_path, _rename_event_key("type", "event_type"))
    lines = [digest._format_gc_event_line(e, TODAY) for e in digest._load_gc_events(str(path), 14, TODAY.isoformat())]
    assert lines
    assert_unguarded(not any("Sample Opponent" in line for line in lines), "G14c")
    assert_unguarded(all(e["type"] == "" for e in gc_events(data, ROSTER, TODAY, 14)), "G14c")

