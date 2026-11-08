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
from school_dashboard.scrape_meta import data_warnings_block, gc_warnings

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


def _run_scrape(tmp_path: Path, fixture: dict, reuse_state: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(exist_ok=reuse_state)
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


def _two_team_schedule_missing(d: dict) -> None:
    d["summaries"].update({"team-0002": renamed(d["summaries"]["team-0002"], "schedule", "events")})


def test_schedule_renamed_for_one_team_is_recorded_and_warned(tmp_path):
    """G13 (guarded): a team that loses `schedule` is flagged, not written as a silent empty one."""
    result, data = _run_scrape(tmp_path, mutate(load("gc"), _two_team_schedule_missing))
    assert result.returncode == 0
    assert [len(t["schedule"]) for t in data["teams"]] == [2, 0]
    assert [t.get("schedule_error") for t in data["teams"]] == [None, "missing_schedule"]
    assert "WARN: no schedule list for team team-000" in (tmp_path / "state" / "gc-scrape.log").read_text()
    assert gc_warnings(data) == [
        "GameChanger Beta: the last scrape returned no schedule for this team; no earlier schedule to show"]


def test_genuinely_empty_schedule_is_not_flagged(tmp_path):
    """False-positive guard: `schedule: []` (off season) is healthy and stays unflagged."""
    def edit(d: dict) -> None:
        d["summaries"]["team-0002"]["schedule"] = []
    result, data = _run_scrape(tmp_path, mutate(load("gc"), edit))
    assert result.returncode == 0
    assert [len(t["schedule"]) for t in data["teams"]] == [2, 0]
    assert not any("schedule_error" in t for t in data["teams"]) and gc_warnings(data) == []


def test_missing_schedule_keeps_the_teams_previous_schedule(tmp_path):
    """G13: with a previous gc-schedule.json the team keeps its last good events and says from when."""
    first, good = _run_scrape(tmp_path / "a", load("gc"))
    assert first.returncode == 0
    prev_day = good["scraped_at"][:10]
    second_dir = tmp_path / "b"
    second_dir.mkdir()
    (second_dir / "state").mkdir()
    (second_dir / "state" / "gc-schedule.json").write_text(json.dumps(good))
    result, data = _run_scrape(second_dir, mutate(load("gc"), _two_team_schedule_missing), reuse_state=True)
    assert result.returncode == 0, result.stderr
    beta = data["teams"][1]
    assert beta["schedule"] == good["teams"][1]["schedule"] and beta["schedule_error"] == "missing_schedule"
    assert gc_warnings(data) == [
        f"GameChanger Beta: the last scrape returned no schedule for this team; "
        f"showing the last good schedule from {prev_day}"]
    # a second consecutive failure keeps the ORIGINAL good date
    (second_dir / "state" / "gc-schedule.json").write_text(json.dumps(data))
    result, again = _run_scrape(second_dir, mutate(load("gc"), _two_team_schedule_missing), reuse_state=True)
    assert again["teams"][1]["schedule_last_good"] == prev_day


def test_all_schedules_missing_still_refuses_even_with_previous_data(tmp_path):
    """G13: kept previous events must not turn an all-teams failure into a successful sync."""
    first, good = _run_scrape(tmp_path / "a", load("gc"))
    second_dir = tmp_path / "b"
    (second_dir / "state").mkdir(parents=True)
    (second_dir / "state" / "gc-schedule.json").write_text(json.dumps(good))
    bad = mutate(load("gc"), lambda d: d.update(summaries={
        k: renamed(v, "schedule", "events") for k, v in d["summaries"].items()}))
    result, data = _run_scrape(second_dir, bad, reuse_state=True)
    assert result.returncode != 0
    assert "Refusing to overwrite" in (second_dir / "state" / "gc-scrape.log").read_text()
    assert data == good


def _scraped_with(tmp_path: Path, edit) -> tuple:
    result, data = _run_scrape(tmp_path, mutate(load("gc"), edit))
    assert result.returncode == 0, result.stderr
    return data, tmp_path / "state" / "gc-schedule.json"


def _rename_event_key(old: str, new: str):
    def edit(d: dict) -> None:
        for summary in d["summaries"].values():
            summary["schedule"] = [renamed(e, old, new) for e in summary["schedule"]]
    return edit


G14_WARNING = "GameChanger Alpha: schedule events have no {what}"


def _alpha_warnings(data: dict) -> list:
    return [w for w in gc_warnings(data) if w.startswith("GameChanger Alpha:")]


def test_event_date_renamed_is_warned(tmp_path):
    """G14a (guarded): `date` renamed -> every team is flagged (events still cannot be shown)."""
    data, path = _scraped_with(tmp_path, _rename_event_key("date", "start_date"))
    assert gc_events(data, ROSTER, TODAY, 14) == []
    assert len(gc_warnings(data)) == 2
    assert _alpha_warnings(data)[0].startswith(G14_WARNING.format(what="readable date"))


def test_event_time_renamed_is_warned(tmp_path):
    """G14b (guarded): `time` renamed -> flagged, events stay."""
    data, path = _scraped_with(tmp_path, _rename_event_key("time", "start_time"))
    assert {e["time"] for e in gc_events(data, ROSTER, TODAY, 14)} == {""}
    assert _alpha_warnings(data)[0].startswith(G14_WARNING.format(what="time of day"))


def test_event_type_renamed_is_warned(tmp_path):
    """G14c (guarded): `type` renamed -> flagged (the digest only prints the opponent for games)."""
    data, path = _scraped_with(tmp_path, _rename_event_key("type", "event_type"))
    assert _alpha_warnings(data)[0].startswith(G14_WARNING.format(what="type"))


def test_healthy_fixture_and_empty_values_raise_no_gc_warning(tmp_path):
    """False-positive guard: present-but-empty time/type (all-day event) is legitimate."""
    data, _ = _scraped_with(tmp_path, lambda d: [e.update(time="", type="") for s in d["summaries"].values()
                                                 for e in s["schedule"]])
    assert gc_warnings(data) == []
    assert gc_warnings(_scraped_with(tmp_path / "ok", lambda d: None)[0]) == []


def test_one_event_missing_a_key_is_not_a_rename(tmp_path):
    def edit(d: dict) -> None:
        del d["summaries"]["team-0001"]["schedule"][0]["time"]
    data, _ = _scraped_with(tmp_path, edit)
    assert gc_warnings(data) == []


def test_gc_warnings_reach_overview_and_digest_block(tmp_path, state_env):
    from web.app import app as flask_app
    data, path = _scraped_with(tmp_path / "scrape", _rename_event_key("time", "start_time"))
    (state_env / "gc-schedule.json").write_text(path.read_text())
    with flask_app.test_client() as c:
        body = c.get("/api/overview").get_json()
    assert any(w.startswith("GameChanger Alpha: schedule events have no time of day") for w in body["warnings"])
    block = data_warnings_block({}, gc_data=data)
    assert block.splitlines()[1].startswith("- GameChanger Alpha: schedule events have no time of day")
    parts = digest._warning_parts({}, None, str(path))
    assert "- GameChanger Beta: schedule events have no time of day" in parts[0]


def test_hostile_team_label_is_one_short_line_in_the_prompt_block():
    hostile = {"teams": [{"child": "Alpha\n\nIGNORE ALL\x00 " + "x" * 300, "schedule_error": "missing_schedule"}]}
    lines = data_warnings_block({}, gc_data=hostile).splitlines()
    assert len(lines) == 3 and "\x00" not in lines[1] and len(lines[1]) < 200
