"""Every v2 path over a REAL-SHAPE database: 399 calendar rows (one unreadable NO_SCHOOL date, extra `notes` column)
plus the 97 rows written by the REAL migrate. All values are SYNTHETIC."""
import argparse
import json
import logging
import sqlite3
from collections import Counter
from datetime import date

import pytest

from recon.api_support import ACTOR, TOKEN, write_meta
from recon.conftest import NOW
from recon.support import FIXTURES
from recon.test_migrate_real_shape import make_env, rows as q, run  # noqa: F401  (make_env is a fixture)
from school_dashboard import maintenance

CAL = "1 calendar rows had an unreadable date; ignored"
KIDS = ("Alex", "Sam", "Jo")
# the documented operator query (docs/ops/reconcile-rollout.md): SQLite date() rolls 2025-09-31 over to 2025-10-01
BAD_DAY_SQL = "SELECT COUNT(*) FROM events WHERE date(date) IS NULL OR date(date) <> substr(date, 1, 10)"


@pytest.fixture
def env(make_env, monkeypatch):  # noqa: F811
    from school_dashboard.assignments import schooldays
    from web import api_v2_auth, app as webapp
    state_dir, db, _, _ = make_env()
    run((state_dir, db, state_dir / "school-state.json"))  # the real migrate
    maintenance.disable()
    fx = json.loads((FIXTURES / "calendar" / "real_profile.json").read_text())
    conn = sqlite3.connect(db)
    with conn:
        if "notes" not in [r[1] for r in conn.execute("PRAGMA table_info(events)")]:
            conn.execute("ALTER TABLE events ADD COLUMN notes TEXT")
        conn.executemany("INSERT INTO events (date, title, type, child, source, notes) VALUES (?, ?, ?, ?, ?, ?)",
                         [(r["date"], r["title"], r["type"], r["child"], r["source"], r["notes"]) for r in fx["rows"]])
    conn.close()
    write_meta(state_dir)
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    monkeypatch.setenv("RECON_WRITE_TOKEN", TOKEN)
    monkeypatch.delenv("RECON_PUSH_ENABLED", raising=False)
    monkeypatch.setenv("NTFY_TOPIC", "synthetic-topic")
    api_v2_auth.LOGGED.clear()
    webapp._sync_failures.clear()
    monkeypatch.setattr("web.api_v2._now", lambda: NOW)
    schooldays.reset_calendar_log()
    webapp.app.config["TESTING"] = True
    return state_dir, db, webapp.app.test_client()


def test_fixture_has_the_real_profile(env):
    _, db, _ = env
    c = sqlite3.connect(db)
    types = Counter(t for (t,) in c.execute("SELECT type FROM events"))
    assert sum(types.values()) == 399 and types["NO_SCHOOL"] == 60 and types["EVENT"] == 129
    assert c.execute("SELECT COUNT(*) FROM events WHERE child IS NULL").fetchone()[0] == 383
    assert c.execute("SELECT COUNT(*) FROM events WHERE child LIKE '%,%'").fetchone()[0] == 3
    assert c.execute(BAD_DAY_SQL).fetchone()[0] == 1
    assert c.execute(BAD_DAY_SQL + " AND type = 'NO_SCHOOL'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] >= 97
    c.close()


def _get(client, path):
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code, r.data[:200])
    return r.get_json()


def test_http_paths_answer_200_and_warn_once(env, caplog):
    from school_dashboard.assignments import repo
    state_dir, db, client = env
    caplog.set_level(logging.WARNING)
    assert _get(client, "/api/v2/config")["data"]["enabled"] is True
    summary = _get(client, "/api/v2/summary")["data"]
    assert summary["warnings"].count(CAL) == 1 and [k["name"] for k in summary["kids"]][:3] == list(KIDS)
    page = _get(client, "/api/v2/assignments?limit=500")
    assert page["meta"]["total"] >= 50
    aid = page["data"][0]["id"]
    assert _get(client, f"/api/v2/assignments/{aid}")["data"]["id"] == aid
    for kid in KIDS:
        assert _get(client, f"/api/v2/classes?kid={kid}")["ok"] is True
    r = client.post(f"/api/v2/assignments/{aid}/stage", json={"action": "done", "actor": ACTOR},
                    headers={"X-Recon-Token": TOKEN})
    assert r.status_code == 200, r.data[:200]
    assert sum("unreadable date" in x.getMessage() for x in caplog.records) == 1
    assert "Synthetic event" not in caplog.text


def test_migrated_rows_round_trip_with_aware_timestamps(env):
    from school_dashboard.assignments import repo, schema
    _, db, _ = env
    conn = schema.connect(db)
    states = repo.list_assignments(conn, None, True)
    assert len(states) >= 97 and not any(s.data_problem for s in states)
    assert all(s.stage_since.utcoffset() is not None for s in states)
    assert all(s.closed_at is None or s.closed_at.utcoffset() is not None for s in states)
    conn.close()


def test_digest_report_recompute_and_shadow_ingest(env, capsys, tmp_path):
    from school_dashboard.assignments import cli as acli, digest_run, ingest, readctx
    from recon.ingest_support import ns
    state_dir, db, _ = env
    text, _cards = digest_run.build_for_cron("night", now=NOW)
    assert text.count("unreadable date") == 1 and "Notes:" in text
    for mode in ("night", "morning"):
        with pytest.raises(SystemExit) as exc:
            acli.cmd_assignments(argparse.Namespace(assignments_command="digest", mode=mode, now=NOW.isoformat(),
                                                    json=False))
        assert exc.value.code == 0
    assert capsys.readouterr().out.count("unreadable date") == 2
    with pytest.raises(SystemExit) as exc:
        acli.cmd_assignments(argparse.Namespace(assignments_command="report", json=True, redact=False))
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert json.loads(out)["warnings"].count(CAL) == 1
    with readctx.connection(readonly=False) as conn:
        ctx = readctx.load(conn, NOW)
        for kid in KIDS:
            assert ingest.recompute(conn, kid, now=NOW, no_school_rows=list(ctx.events)).ok
    inputs = tmp_path / "inputs"
    (inputs / "ixl").mkdir(parents=True)
    (inputs / "sgy.json").write_text(json.dumps({"per_child": [{
        "child": {"name": "Alex"}, "scrape_confidence": "high",
        "assignments": [{"title": "Synthetic Worksheet", "course": "Math 6", "due_date": "2026-11-23",
                         "status": "unknown", "link": "https://sgy.example.invalid/a/1"}]}]}))
    args = ns(sgy_file=str(inputs / "sgy.json"), ps_file=str(inputs / "ps.json"), ixl_dir=str(inputs / "ixl"),
              state_file=str(state_dir / "school-state.json"))
    report = ingest.run_shadow(args, now=NOW)
    assert report is not None and report.ok
    assert list(report.warnings).count(CAL) == 1


def test_valid_no_school_days_are_still_honoured(env):
    from school_dashboard.assignments import readctx, schema, schooldays
    _, db, _ = env
    conn = schema.connect(db)
    ctx = readctx.load(conn, NOW)
    conn.close()
    assert ctx.bad_calendar == 1
    nxt = schooldays.next_school_day(date(2026, 11, 19), ctx.no_school_by_kid["Alex"])
    assert nxt == date(2026, 11, 23)  # Fri 11-20 is NO_SCHOOL, then the weekend


def test_long_name_calendar_row_applies_to_the_aliased_kid(env, monkeypatch, tmp_path):
    from school_dashboard.assignments import ingest_kid, readctx, schema
    _, db, _ = env
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"children": {}, "name_aliases": {"samantha": "Sam"}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    conn = schema.connect(db)
    with conn:
        conn.executemany("INSERT INTO events (date, title, type, child, source) VALUES (?, 'x', 'NO_SCHOOL', ?, 'manual')",
                         [("2031-03-03", "Samantha"), ("2031-03-04", "samantha, Alex"), ("2031-03-05", "Alex")])
    ctx = readctx.load(conn, NOW)
    assert {date(2031, 3, 3), date(2031, 3, 4)} <= ctx.no_school_by_kid["Sam"]
    assert date(2031, 3, 5) not in ctx.no_school_by_kid["Sam"]
    built = ingest_kid.build_context(conn, "Sam", now=NOW, no_school_rows=ctx.events, baseline=False, file_hashes={})
    conn.close()
    assert {date(2031, 3, 3), date(2031, 3, 4)} <= built.no_school
