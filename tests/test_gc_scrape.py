"""Runs the REAL sync/gc-scrape.sh against a stub `gc` (no network, no creds).

Regression: the script used to pass the growing result JSON as python3 argv,
which dies with 'Argument list too long' once a single string exceeds 128 KiB.
"""
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "sync" / "gc-scrape.sh"
N_TEAMS = 11
N_EVENTS = 60


def _make_env(tmp_path: Path, *, events: int = N_EVENTS, fail_team: str = "", team_map: str = ""):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (bindir / "school-state").write_text("#!/bin/sh\nexit 10\n")  # maintenance inactive
    (bindir / "school-state").chmod(0o755)
    gc = bindir / "gc"
    gc.write_text(f"""#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
if a[0] == "teams":
    print(json.dumps([{{"id": f"t{{i}}", "name": f"Team {{i}}"}} for i in range({N_TEAMS})]))
elif a[0] == "summary":
    tid = a[a.index("--team") + 1]
    if tid == {fail_team!r}:
        sys.exit(1)
    print(json.dumps({{"schedule": [
        {{"date": "2026-11-%02d" % (e % 28 + 1), "type": "game", "opponent": "Opp %d" % e,
          "location": "L" * 400, "notes": "N" * 400}} for e in range({events})]}}))
""")
    gc.chmod(0o755)
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path),
        "SCHOOL_DASHBOARD_ENV": "/nonexistent", "SCHOOL_STATE_DIR": str(state),
        "SCHOOL_GC_PATH": str(state / "gc-schedule.json"),
    }
    if team_map:
        env["GC_TEAM_MAP"] = team_map
    return env, state


def _run(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def test_large_output_does_not_hit_argv_limit(tmp_path):
    team_map = ",".join(f"t{i}:Kid{i}" for i in range(N_TEAMS))
    env, state = _make_env(tmp_path, team_map=team_map)
    r = _run(env)
    log = (state / "gc-scrape.log").read_text()
    assert r.returncode == 0, r.stderr
    assert "Argument list too long" not in log + r.stderr
    out = state / "gc-schedule.json"
    assert out.stat().st_size > 400 * 1024
    data = json.loads(out.read_text())
    assert set(data) == {"scraped_at", "teams"}
    assert len(data["teams"]) == N_TEAMS
    assert [t["child"] for t in data["teams"]] == [f"Kid{i}" for i in range(N_TEAMS)]
    assert all(set(t) == {"team_id", "team_name", "child", "schedule"} for t in data["teams"])
    assert sum(len(t["schedule"]) for t in data["teams"]) == N_TEAMS * N_EVENTS
    assert f"{N_TEAMS * N_EVENTS} event(s)" in log


def test_all_empty_schedules_refuse_overwrite(tmp_path):
    env, state = _make_env(tmp_path, events=0)
    old = state / "gc-schedule.json"
    old.write_text('{"old": true}')
    r = _run(env)
    assert r.returncode != 0
    assert old.read_text() == '{"old": true}'
    assert "Refusing to overwrite" in (state / "gc-scrape.log").read_text()


def test_failing_team_is_skipped_others_written(tmp_path):
    env, state = _make_env(tmp_path, fail_team="t3")
    r = _run(env)
    assert r.returncode == 0, r.stderr
    data = json.loads((state / "gc-schedule.json").read_text())
    ids = [t["team_id"] for t in data["teams"]]
    assert len(ids) == N_TEAMS - 1 and "t3" not in ids
    assert "skipping" in (state / "gc-scrape.log").read_text()


def test_unmapped_team_warns_and_falls_back_to_team_name(tmp_path):
    env, state = _make_env(tmp_path, team_map="t0:Kid0")
    assert _run(env).returncode == 0
    data = json.loads((state / "gc-schedule.json").read_text())
    assert data["teams"][0]["child"] == "Kid0"
    assert data["teams"][1]["child"] == "Team 1"
    assert "not in GC_TEAM_MAP" in (state / "gc-scrape.log").read_text()


@pytest.mark.parametrize("events,expected", [(5, "ok"), (0, "error")])
def test_api_sync_records_honest_gc_status(tmp_path, monkeypatch, events, expected):
    import web.app as app_module
    from school_dashboard.sync_meta import read_sync_meta

    env, state = _make_env(tmp_path, events=events)
    meta = tmp_path / "meta.json"
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("SCHOOL_DASHBOARD_ENV", "/nonexistent")
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(state))
    monkeypatch.setenv("SCHOOL_GC_PATH", env["SCHOOL_GC_PATH"])
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(meta))
    monkeypatch.setenv("GC_SCRAPE_SCRIPT", str(SCRIPT))
    monkeypatch.setenv("SOURCE_GC_ENABLED", "1")
    if app_module._sync_lock.locked():
        app_module._sync_lock.release()
    assert app_module._sync_lock.acquire(blocking=False)
    app_module._run_sync_background("gc", "none")
    assert read_sync_meta(str(meta))["gc"]["last_result"] == expected
    assert app_module._sync_status["last_result"] == expected
