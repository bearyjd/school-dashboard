#!/usr/bin/env python3
"""Synthetic-data server for SPA screenshots, axe checks and manual QA.

Builds a throwaway state dir from invented data (three kids, a PowerSchool fixture, GameChanger
events, school events, manual items, one stale source) through the real `school-state update`
pipeline, then serves the real Flask app (API + built SPA at /app/) on 127.0.0.1.

    npm --prefix web/spa run build
    python3 scripts/spa-qa-server.py --port 5099      # then open http://127.0.0.1:5099/app/

No real credentials, names or network access are used or needed.
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
KIDS = {
    "Alex": {"grade": "7th", "school": "Test Middle"},
    "Jack": {"grade": "5th", "school": "Test Elementary"},
    "Jo": {"grade": "3rd", "school": "Test Elementary"},
}
STALE_HOURS = 20


def _day(today: date, offset: int) -> str:
    return (today + timedelta(days=offset)).isoformat()


def _write_inputs(inputs: Path, today: date) -> None:
    (inputs / "ixl").mkdir(parents=True)
    (inputs / "ixl" / "alex-assigned.json").write_text(json.dumps(
        {"totals": {"Math": {"assigned": 4, "done": 1, "remaining": 3}}, "remaining": []}))
    (inputs / "sgy.json").write_text(json.dumps({"per_child": [
        {"child": {"name": "Alex Tester"}, "grades": [], "announcements": [], "assignments": [
            {"title": "Water cycle essay", "course": "Science", "due_date": _day(today, -3), "status": "open"},
            {"title": "Read chapter 4", "course": "ELA", "due_date": _day(today, 1), "status": "open"}]},
        {"child": {"name": "Jack Tester"}, "grades": [], "announcements": [], "assignments": [
            {"title": "Map project " + "x" * 120, "course": "History", "due_date": _day(today, -1), "status": "open"}]},
    ]}))
    ps = json.loads((ROOT / "tests" / "fixtures" / "powerschool-summary.json").read_text())
    ps["scraped_at"] = datetime.now().isoformat(timespec="seconds")
    for child in ps["children"]:
        for key, offset in (("missing", -9), ("late", -4), ("upcoming", 2)):
            for entry in child[key]:
                entry["due"] = _day(today, offset)
    (inputs / "ps.json").write_text(json.dumps(ps))


def _write_state(state_dir: Path, today: date) -> None:
    from school_dashboard import cli, state
    from school_dashboard.db import create_item, init_db
    from school_dashboard.sync_meta import write_sync_source

    inputs = state_dir.parent / "inputs"
    _write_inputs(inputs, today)
    argv = ["school-state", "update", "--ixl-dir", str(inputs / "ixl"),
            "--sgy-file", str(inputs / "sgy.json"), "--ps-file", str(inputs / "ps.json")]
    state._config_cache = None
    with patch.object(sys, "argv", argv):
        cli.main()

    db = str(state_dir / "school.db")
    init_db(db)
    create_item(db, "Jo", "Poster for science fair", due_date=_day(today, -1))
    create_item(db, "Jack", "Bring gym shoes", due_date=_day(today, 1))
    conn = sqlite3.connect(db)
    with conn:
        conn.executemany("INSERT INTO events (date, title, type, child, source) VALUES (?, ?, ?, ?, 'manual')", [
            (_day(today, 3), "No school", "HOLIDAY", None),
            (_day(today, 5), "Spring concert", "EVENT", "Jo"),
        ])
    conn.close()

    def game(offset: int, time: str, opponent: str) -> dict:
        return {"date": _day(today, offset), "time": time, "type": "game", "opponent": opponent,
                "location": "Field 1", "home_away": "home"}

    (state_dir / "gc-schedule.json").write_text(json.dumps({
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "teams": [
            {"team_id": "t1", "team_name": "Lions", "child": "Alex", "schedule": [game(1, "16:00", "Hawks"), game(8, "10:00", "Bears")]},
            {"team_id": "t2", "team_name": "Cubs", "child": "Jack+Jo", "schedule": [game(2, "18:30", "Owls")]},
        ]}))

    for source in ("ixl", "sgy", "gc", "powerschool"):
        write_sync_source(source, "ok")
    meta_path = state_dir / "sync_meta.json"
    meta = json.loads(meta_path.read_text())
    old = (datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)).isoformat(timespec="seconds")
    meta["ixl"].update({"last_run": old, "last_ok": old})
    meta_path.write_text(json.dumps(meta))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=5099)
    args = parser.parse_args()

    if not (ROOT / "web" / "spa" / "dist" / "index.html").exists():
        sys.exit("SPA not built. Run: npm --prefix web/spa run build")

    tmp = Path(tempfile.mkdtemp(prefix="spa-qa-"))
    state_dir = tmp / "state"
    state_dir.mkdir()
    cfg = tmp / "roster.json"
    cfg.write_text(json.dumps({"children": KIDS}))
    for var in [v for v in os.environ if v.startswith("SCHOOL_")]:
        del os.environ[var]
    os.environ.update({"SCHOOL_STATE_DIR": str(state_dir), "SCHOOL_DASHBOARD_CONFIG": str(cfg), "SYNC_TOKEN": ""})

    sys.path.insert(0, str(ROOT))
    _write_state(state_dir, date.today())
    from web.app import app

    print(f"Synthetic state in {state_dir}; serving http://127.0.0.1:{args.port}/app/", file=sys.stderr, flush=True)
    app.run(host="127.0.0.1", port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
