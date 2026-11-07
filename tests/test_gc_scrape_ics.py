"""Runs the REAL sync/gc-scrape.sh with a stub `gc` and a local fake ICS feed server."""
import json
import os
import site
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "sync" / "gc-scrape.sh"
FAKE_TOKEN = "FAKESECRETTOKEN123"

ICS = (
    "BEGIN:VCALENDAR\r\nX-WR-CALNAME:Feed Team Name\r\n"
    "BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20261113T001500Z\r\nSUMMARY:Lions Practice\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
API_EVENT = {"date": "2026-11-20", "time": "18:00", "type": "game", "opponent": "Hawks",
             "location": "Gym", "home_away": "home"}


@pytest.fixture
def server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar")
            self.end_headers()
            self.wfile.write(ICS.encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()


def _setup(tmp_path, port, *, teams, summaries, feeds, teams_fail=False):
    """summaries: {team: 'fail' | 'empty' | 'ok'}; feeds: iterable of team ids."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (bindir / "school-state").write_text(
        f'#!/bin/sh\n[ "$1" = gc-ics ] && exec {sys.executable} -m school_dashboard.cli "$@"\n'
        f'[ "$1" = gc-fallback-alert ] && {{ echo "$@" >> {tmp_path}/alerts.log; exit 0; }}\nexit 10\n')
    (bindir / "school-state").chmod(0o755)
    (bindir / "gc").write_text(f"""#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
if a[0] == "teams":
    if {teams_fail!r}:
        sys.exit(1)
    print(json.dumps([{{"id": t, "name": "N-" + t}} for t in {list(teams)!r}]))
else:
    tid = a[a.index("--team") + 1]
    mode = {summaries!r}.get(tid, "ok")
    if mode == "fail":
        sys.exit(1)
    print(json.dumps({{"schedule": [] if mode == "empty" else [{API_EVENT!r}]}}))
""")
    (bindir / "gc").chmod(0o755)
    cfg = tmp_path / "ics-feeds.json"
    cfg.write_text(json.dumps({t: f"http://127.0.0.1:{port}/f.ics?teamId={t}&token={FAKE_TOKEN}" for t in feeds}))
    cfg.chmod(0o600)
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path), "PYTHONPATH": str(REPO),
        "PYTHONUSERBASE": site.getuserbase(),  # HOME is faked; keep user-site packages importable
        "SCHOOL_DASHBOARD_ENV": "/nonexistent", "SCHOOL_STATE_DIR": str(state),
        "SCHOOL_GC_PATH": str(state / "gc-schedule.json"),
        "GC_ICS_FEEDS": str(cfg), "GC_ICS_ALLOWED_HOSTS": "127.0.0.1",
        "GC_TEAM_MAP": "tA:Ford,tB:Jack",
    }
    return env, state


def _run(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def _by_team(state):
    data = json.loads((state / "gc-schedule.json").read_text())
    return {t["team_id"]: t for t in data["teams"]}


def _log(state, r):
    log = (state / "gc-scrape.log").read_text()
    assert FAKE_TOKEN not in log + r.stdout + r.stderr
    return log


def test_api_failure_uses_feed_other_team_stays_api(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"], summaries={"tA": "fail"}, feeds=["tA"])
    r = _run(env)
    assert r.returncode == 0, r.stderr
    teams = _by_team(state)
    assert teams["tA"]["schedule_source"] == "ics" and teams["tA"]["child"] == "Ford"
    assert teams["tA"]["schedule"][0]["date"] == "2026-11-12"
    assert teams["tB"]["schedule_source"] == "api" and teams["tB"]["schedule"] == [API_EVENT]
    log = _log(state, r)
    assert "using calendar feed for team tA (api failed)" in log
    assert "feeds only" not in log


def test_api_empty_with_feed_uses_feed(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"], summaries={"tA": "empty"}, feeds=["tA"])
    assert _run(env).returncode == 0
    assert _by_team(state)["tA"]["schedule_source"] == "ics"


def test_empty_without_feed_still_refuses_overwrite(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"],
                        summaries={"tA": "empty", "tB": "empty"}, feeds=[])
    (state / "gc-schedule.json").write_text('{"keep": true}')
    r = _run(env)
    assert r.returncode == 1
    assert json.loads((state / "gc-schedule.json").read_text()) == {"keep": True}


def test_all_ics_exits_zero_with_warning(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"],
                        summaries={"tA": "fail", "tB": "fail"}, feeds=["tA", "tB"])
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "GC API unavailable; schedules from calendar feeds only" in _log(state, r)
    assert {t["schedule_source"] for t in _by_team(state).values()} == {"ics"}


def test_team_list_failure_falls_back_to_feed_teams(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=[], summaries={}, feeds=["tA", "tZ"], teams_fail=True)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    teams = _by_team(state)
    assert set(teams) == {"tA", "tZ"}
    assert teams["tA"]["child"] == "Ford" and teams["tA"]["team_name"] == "Feed Team Name"
    assert teams["tZ"]["child"] == "Feed Team Name"
    _log(state, r)


def test_team_list_failure_without_feeds_fails(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=[], summaries={}, feeds=[], teams_fail=True)
    assert _run(env).returncode == 1


def test_feed_failure_keeps_old_skip_behavior(tmp_path):
    # Feed server not listening: failed API + failed feed => team skipped, other team written.
    env, state = _setup(tmp_path, 9, teams=["tA", "tB"], summaries={"tA": "fail"}, feeds=["tA"])
    r = _run(env)
    assert r.returncode == 0
    assert set(_by_team(state)) == {"tB"}
    _log(state, r)


def _alerts(tmp_path):
    p = tmp_path / "alerts.log"
    return p.read_text().splitlines() if p.exists() else []


def test_alert_requested_when_feed_used(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"], summaries={"tA": "fail"}, feeds=["tA"])
    assert _run(env).returncode == 0
    assert _alerts(tmp_path) == ["gc-fallback-alert --ics-teams 1 --total 2"]


def test_no_alert_when_api_healthy(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"], summaries={}, feeds=["tA"])
    assert _run(env).returncode == 0
    assert _alerts(tmp_path) == []


def test_all_ics_requests_alert_for_all(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"],
                        summaries={"tA": "fail", "tB": "fail"}, feeds=["tA", "tB"])
    assert _run(env).returncode == 0
    assert _alerts(tmp_path) == ["gc-fallback-alert --ics-teams 2 --total 2"]


def test_feed_warnings_are_logged(tmp_path, server, monkeypatch):
    monkeypatch.setitem(globals(), "ICS", ICS.replace("UID:1", "UID:1\r\nRRULE:FREQ=WEEKLY"))
    env, state = _setup(tmp_path, server, teams=["tA"], summaries={"tA": "fail"}, feeds=["tA"])
    r = _run(env)
    assert r.returncode == 0
    assert "WARN: calendar feed tA: 1 recurring event(s) not expanded" in _log(state, r)


def test_feed_only_unmapped_team_warns_with_id(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=[], summaries={}, feeds=["tZ"], teams_fail=True)
    r = _run(env)
    assert r.returncode == 0
    assert "team tZ (tZ) not in GC_TEAM_MAP" in _log(state, r)


def test_empty_feeds_do_not_blame_auth(tmp_path, server, monkeypatch):
    monkeypatch.setitem(globals(), "ICS", "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    env, state = _setup(tmp_path, server, teams=[], summaries={}, feeds=["tA"], teams_fail=True)
    r = _run(env)
    assert r.returncode == 1
    log = _log(state, r)
    assert "API unavailable and calendar feeds empty" in log and "likely auth failure" not in log


def test_bad_perms_config_warns_alerts_and_skips_failed_team(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"], summaries={"tA": "fail"}, feeds=["tA"])
    Path(env["GC_ICS_FEEDS"]).chmod(0o644)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert set(_by_team(state)) == {"tB"}
    log = _log(state, r)
    assert "WARN: calendar feeds disabled:" in log and "chmod 600" in log
    assert "gc summary failed for team tA and no usable calendar feed" in log
    assert _alerts(tmp_path) == ["gc-fallback-alert --config-problem"]


def test_malformed_config_also_alerts(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"], summaries={"tA": "fail"}, feeds=["tA"])
    Path(env["GC_ICS_FEEDS"]).write_text("{nope")
    assert _run(env).returncode == 0
    assert _alerts(tmp_path) == ["gc-fallback-alert --config-problem"]


def test_absent_config_is_not_an_alert(tmp_path, server):
    env, state = _setup(tmp_path, server, teams=["tA", "tB"], summaries={"tA": "fail"}, feeds=["tA"])
    Path(env["GC_ICS_FEEDS"]).unlink()
    assert _run(env).returncode == 0
    assert _alerts(tmp_path) == []


def test_hung_feed_is_killed_by_shell_timeout(tmp_path):
    class Hang(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(30)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Hang)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env, state = _setup(tmp_path, srv.server_port, teams=["tA", "tB"],
                            summaries={"tA": "fail"}, feeds=["tA"])
        env["GC_ICS_TIMEOUT"] = "1"
        r = _run(env)  # server stalls 30s; python's own read timeout is 20s, so only `timeout` can fire
    finally:
        srv.shutdown()
    assert r.returncode == 0
    assert set(_by_team(state)) == {"tB"}
    assert "calendar feed for team tA timed out" in _log(state, r)
