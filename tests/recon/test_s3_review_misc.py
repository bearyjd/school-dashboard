"""S3 review round (push size, text hygiene, report caps, small robustness) and S2 leftovers. SYNTHETIC data."""
import json
import logging
import sqlite3
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from recon.adapter_support import resolve
from recon.ingest_support import WED, make_db
from recon.report_support import cli, db_with, ps, sgy
from recon.test_review_round1 import assert_no_v2, env, run  # noqa: F401  (env is a fixture)

ESC = "\x1b[2J"


# ---- D: push size -------------------------------------------------------------------------

def _pending(state_dir, titles):
    from school_dashboard.assignments import repo, schema
    seed = [sgy(t, "Language Arts", f"s{i}") for i, t in enumerate(titles)]
    path = make_db(state_dir, {"seed": seed})
    conn = schema.connect(path)
    with repo.transaction(conn):
        for (aid,) in conn.execute("SELECT id FROM assignments ORDER BY id").fetchall():
            repo.enqueue_push(conn, aid, "missing", WED, "transition")
    return conn


def _send(conn, send, minutes=5):
    from school_dashboard.assignments import notify
    return notify.send_pending(conn, now=WED + timedelta(minutes=minutes), send=send)


@pytest.fixture
def penv(state_env, monkeypatch):
    monkeypatch.setenv("RECON_PUSH_ENABLED", "1")
    monkeypatch.setenv("NTFY_TOPIC", "synthetic-topic-7f3a91")
    return state_env


def _counts(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM push_outbox GROUP BY status")}


def test_25_pending_rows_go_out_ten_at_a_time(penv):
    conn = _pending(penv, [f"Task {i:02d}" for i in range(25)])
    send = MagicMock()
    _send(conn, send)
    body = send.call_args.args[1].splitlines()
    assert len(body) == 11 and body[-1] == "+15 more" and "Task 09" in body[9]
    assert _counts(conn) == {"sent": 10, "pending": 15}
    _send(conn, send, 6)
    assert send.call_args.args[1].splitlines()[-1] == "+5 more"
    assert _counts(conn) == {"sent": 20, "pending": 5}
    _send(conn, send, 7)
    assert "more" not in send.call_args.args[1] and len(send.call_args.args[1].splitlines()) == 5
    assert _counts(conn) == {"sent": 25}
    assert send.call_count == 3


def test_exactly_ten_rows_has_no_more_line(penv):
    conn = _pending(penv, [f"T{i}" for i in range(10)])
    send = MagicMock()
    _send(conn, send)
    assert "more" not in send.call_args.args[1] and _counts(conn) == {"sent": 10}


def test_long_title_is_cut_to_120_chars_with_an_ellipsis(penv):
    conn = _pending(penv, ["L" * 300])
    send = MagicMock()
    _send(conn, send)
    line = send.call_args.args[1]
    assert len(line) == 120 and line.endswith("…")


def test_body_never_exceeds_3500_bytes_and_only_sent_rows_are_marked(penv):
    conn = _pending(penv, ["\U0001F600" * 290 + str(i) for i in range(10)])
    send = MagicMock()
    _send(conn, send)
    body = send.call_args.args[1]
    assert len(body.encode("utf-8")) <= 3500
    counts = _counts(conn)
    included = len([ln for ln in body.splitlines() if not ln.startswith("+")])
    assert counts["sent"] == included < 10 and counts["pending"] == 10 - included
    assert body.splitlines()[-1] == f"+{10 - included} more"


def test_failed_send_marks_only_the_attempted_rows(penv):
    conn = _pending(penv, [f"T{i}" for i in range(12)])

    def boom(*a):
        raise OSError("x")
    _send(conn, boom)
    assert _counts(conn) == {"failed": 10, "pending": 2}


def test_module_docstring_documents_at_least_once_delivery():
    from school_dashboard.assignments import notify
    assert "at-least-once" in (notify.__doc__ or "")


# ---- E: untrusted text hygiene ----------------------------------------------------------------

def test_clean_text_unit():
    from school_dashboard.assignments.adapters.base import clean_text
    assert clean_text("a" + ESC + "b", 300) == "a[2Jb"
    assert clean_text("a\r\nb\t c", 300) == "a b c"
    assert clean_text("zero​width‮﻿", 300) == "zerowidth"
    assert clean_text("x" * 10000, 300) == "x" * 300
    assert clean_text(None, 10) == "" and clean_text(5, 10) == ""
    assert clean_text("  ab  cd  ", 4) == "ab c"


def _sgy(title="Essay", course="Math 6", status="unknown"):
    from school_dashboard.assignments.adapters import ADAPTERS
    payload = {"per_child": [{"child": {"name": "Alex"}, "scrape_confidence": "high", "assignments": [
        {"title": title, "course": course, "due_date": "2026-11-20", "status": status, "link": ""}]}]}
    return ADAPTERS["schoology"].parse(payload, resolve_kid=resolve)


def test_schoology_cleans_title_and_class_and_caps_them():
    rec = _sgy(ESC + "Essay\r\nDraft​" + "z" * 10000, "Math\x1b[31m\n6" + "c" * 500).records[0]
    assert "\x1b" not in rec.title and "\n" not in rec.title and "​" not in rec.title and len(rec.title) == 300
    assert rec.title.startswith("[2JEssay Draft")
    assert rec.class_name is not None and len(rec.class_name) == 120 and "\x1b" not in rec.class_name


def test_schoology_raw_status_is_capped_at_80():
    assert len(_sgy(status="graded" + "s" * 500).records[0].raw_status) == 80


def test_schoology_title_blank_after_cleaning_is_skipped_with_a_count():
    res = _sgy("\x1b​")
    assert res.records == () and any("without a title skipped" in w for w in res.warnings)


def test_powerschool_cleans_title_class_and_score():
    from school_dashboard.assignments.adapters.powerschool import PowerSchoolAdapter
    item = {"course": "Sci" + ESC, "title": "Lab\r\n" + ESC + "x" * 1000, "due": "2026-11-20", "score": "9" * 500}
    payload = {"children": [{"name": "Alex", "missing": [item], "late": [], "upcoming": [], "warnings": []}]}
    res = PowerSchoolAdapter().parse(payload, resolve_kid=resolve)
    rec = res.records[0]
    assert "\x1b" not in rec.title and len(rec.title) == 300 and "\x1b" not in (rec.class_name or "")
    assert rec.score is not None and len(rec.score) == 160


def test_powerschool_title_blank_after_cleaning_makes_the_entry_untrusted():
    from school_dashboard.assignments.adapters.powerschool import PowerSchoolAdapter
    item = {"course": "Sci", "title": "​\x1b", "due": "2026-11-20", "score": None}
    payload = {"children": [{"name": "Alex", "missing": [item], "late": [], "upcoming": [], "warnings": []}]}
    res = PowerSchoolAdapter().parse(payload, resolve_kid=resolve)
    assert res.records == () and "Alex" not in res.trusted_kids


def test_ixl_subject_is_cleaned():
    from school_dashboard.assignments.adapters import ADAPTERS
    payload = {"Alex": {"status": "ok", "totals": {"Math" + ESC: {"assigned": 2, "done": 1, "remaining": 1}}}}
    rec = ADAPTERS["ixl"].parse(payload, resolve_kid=resolve).records[0]
    assert "\x1b" not in rec.title and "\x1b" not in rec.external_key


def test_report_text_and_alias_list_strip_control_characters(state_env):
    from school_dashboard.assignments import report
    path = db_with(state_env, [sgy("Evil" + ESC + "\rTitle", "Cls" + ESC, "s1"), ps("Evil" + ESC + "\rTitle", "Other", "p1")],
                   setup={"class_settings": [{"kid": "Alex", "class_key": "cls", "aliases": ["al" + ESC + "ias"]}]})
    rep = report.run(path, now=WED, meta={})
    text = report.render_text(rep)
    assert "Evil" in text and "\x1b" not in text and "\r" not in text
    out = cli(state_env, "alias", "list")
    assert out.returncode == 0 and "alias" not in out.stderr and "\x1b" not in out.stdout and "al" in out.stdout


# ---- F: report caps ---------------------------------------------------------------------------

def _rows(n_each, due=lambda i: date(2026, 11, 20), title=lambda i: "Same Title", kid="Alex"):
    from school_dashboard.assignments.report import AssignmentRow, LinkRow
    rows = []
    for i in range(n_each):
        for j, src in enumerate(("schoology", "powerschool")):
            rows.append(AssignmentRow(id=i * 2 + j, kid=kid, class_name=f"C{src}", class_key=f"c{src}", title=title(i),
                                      title_key=title(i).casefold(), due_date=due(i), stage=1, flag=None,
                                      links=(LinkRow(src, "open", True),)))
    return rows


def _build(rows):
    from datetime import datetime
    from school_dashboard.assignments.health import IngestHealth
    from school_dashboard.assignments.report import ReportInput, build_report
    return build_report(ReportInput(tuple(rows), {}, {}), IngestHealth(True, None, None))


def test_unmatched_pairs_are_capped_at_500_with_a_truncated_flag():
    rep = _build(_rows(40))  # 40 x 40 cross-source pairs
    assert len(rep["unmatched_pairs"]) == 500 and rep["unmatched_truncated"] is True
    small = _build(_rows(3))
    assert len(small["unmatched_pairs"]) == 9 and small["unmatched_truncated"] is False


def test_candidates_are_bucketed_by_kid_and_due_date(monkeypatch):
    from school_dashboard.assignments import report
    calls = []
    real = report._pair
    monkeypatch.setattr(report, "_pair", lambda a, b: calls.append(1) or real(a, b))
    report.unmatched_pairs(_rows(10, due=lambda i: date(2026, 11, 1) + timedelta(days=i)))
    assert len(calls) == 10  # one comparison per due date, not C(20, 2) = 190


def test_only_the_first_120_chars_of_a_title_key_are_compared(monkeypatch):
    from school_dashboard.assignments import report
    seen = []
    real = report.SequenceMatcher

    def spy(isjunk, a, b, *rest, **kw):
        seen.append((len(a), len(b)))
        return real(isjunk, a, b, *rest, **kw)
    monkeypatch.setattr(report, "SequenceMatcher", spy)
    report.unmatched_pairs(_rows(2, title=lambda i: "x" * 10000 + str(i)))
    assert seen and all(a <= 120 and b <= 120 for a, b in seen)


# ---- G2..G6 ------------------------------------------------------------------------------------

def test_overview_survives_a_broken_ingest_warning_call(state_env, tmp_path, monkeypatch, caplog):
    from school_dashboard import state
    from school_dashboard.assignments import health
    from web.app import app
    cfg = tmp_path / "roster.json"
    cfg.write_text(json.dumps({"children": {"Alpha": {"grade": "1st", "school": "S"}}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.setattr(state, "_config_cache", None)
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    monkeypatch.setattr(health, "ingest_warnings", lambda *a, **k: (_ for _ in ()).throw(KeyError("secret")))
    caplog.set_level(logging.ERROR)
    resp = app.test_client().get("/api/overview")
    assert resp.status_code == 200
    assert "Assignment merge status unavailable" in resp.get_json()["warnings"]
    assert "KeyError" in caplog.text and "secret" not in caplog.text


def test_connect_closes_the_connection_when_a_pragma_raises(tmp_path, monkeypatch):
    from school_dashboard.assignments import schema
    closed = []

    class Fake:
        row_factory = None

        def execute(self, sql, *a):
            if "busy_timeout" in sql:
                raise sqlite3.OperationalError("boom")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(schema.sqlite3, "connect", lambda *a, **k: Fake())
    with pytest.raises(sqlite3.OperationalError):
        schema.connect(str(tmp_path / "x.db"))
    assert closed == [True]


def test_report_shows_the_baseline_state(tmp_path):
    from school_dashboard.assignments import report
    on = report.render_text(report.run(make_db(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path), now=WED, meta={}))
    assert "baseline: set 2026-11-01" in on
    off = report.render_text(report.run(make_db(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path,
                                                baseline=False), now=WED, meta={}))
    assert "baseline: NOT set (immediate pushes are off)" in off


def test_alias_names_are_capped_at_100_chars(state_env, capsys):
    from school_dashboard.assignments import aliases, schema
    path = make_db(state_env, {"seed": [sgy("Essay", "Language Arts", "s1")]})
    conn = schema.connect(path)
    with pytest.raises(aliases.AliasError):
        aliases.add(conn, kid="Alex", from_name="a" * 101, to_name="Language Arts")
    with pytest.raises(aliases.AliasError):
        aliases.add(conn, kid="Alex", from_name="ELA", to_name="b" * 101)
    aliases.add(conn, kid="Alex", from_name="a" * 100, to_name="Language Arts")
    conn.close()
    from school_dashboard import cli as main_cli
    with patch.object(sys, "argv", ["school-state", "assignments", "alias", "add", "--kid", "Alex",
                                    "--from", "z" * 101, "--to", "Language Arts"]), pytest.raises(SystemExit) as exc:
        main_cli.main()
    assert exc.value.code == 2


def test_alias_add_logs_one_info_line_with_counts_only(state_env, caplog):
    from school_dashboard import cli as main_cli
    make_db(state_env, {"seed": [sgy("Essay", "Language Arts", "s1")]})
    caplog.set_level(logging.INFO)
    with patch.object(sys, "argv", ["school-state", "assignments", "alias", "add", "--kid", "Alex",
                                    "--from", "SecretAlias", "--to", "Language Arts"]), pytest.raises(SystemExit):
        main_cli.main()
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "alias" in r.getMessage()]
    assert len(infos) == 1 and "SecretAlias" not in caplog.text and "Alex" not in caplog.text


def test_redact_uses_a_random_salt_per_run(tmp_path):
    from school_dashboard.assignments import report
    path = db_with(tmp_path, [sgy("Essay Draft", "Language Arts", "s1"), ps("Essay Draft", "ELA", "p1")])
    rep = report.run(path, now=WED, meta={})
    one, two = report.redact(rep), report.redact(rep)
    assert one["unmatched_pairs"][0]["title_a"] != two["unmatched_pairs"][0]["title_a"]
    pair = one["unmatched_pairs"][0]
    assert pair["title_a"] == pair["title_b"]  # internally consistent within one report
    assert one["alias_suggestions"][0]["class_a"] in {pair["class_a"], pair["class_b"]}


# ---- I: S2 leftovers -----------------------------------------------------------------------------

def test_items_changed_between_plan_rows_and_sha_is_caught(env, monkeypatch):
    from school_dashboard.assignments import migrate
    real, fired = migrate.schema.connect, []

    class Cur:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class Spy:
        def __init__(self, conn, path):
            self._c, self._p = conn, path

        def execute(self, sql, *a):
            cur = self._c.execute(sql, *a)
            if not fired and sql.startswith("SELECT * FROM items"):
                rows = cur.fetchall()
                fired.append(1)
                w = sqlite3.connect(self._p)
                w.execute("UPDATE items SET notes = 'changed-mid-plan' WHERE id = (SELECT MIN(id) FROM items)")
                w.commit()
                w.close()
                return Cur(rows)
            return cur

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(migrate.schema, "connect",
                        lambda path, **kw: Spy(real(path, **kw), path) if kw.get("readonly") else real(path, **kw))
    with pytest.raises(migrate.MigrationError, match="items table changed"):
        run(env)
    assert fired
    assert_no_v2(env[1])


def test_state_size_cap_uses_a_bounded_read_not_a_stat(env, monkeypatch):
    from pathlib import Path
    from school_dashboard.assignments import migrate
    monkeypatch.setattr(migrate, "MAX_STATE_BYTES", 10)
    monkeypatch.setattr(Path, "stat", lambda *a, **k: (_ for _ in ()).throw(OSError("no stat")))
    with pytest.raises(migrate.MigrationInputError, match="too large"):
        run(env, dry_run=True)


def test_state_file_exactly_at_the_cap_is_read(env, monkeypatch):
    from school_dashboard.assignments import migrate
    size = env[2].stat().st_size
    monkeypatch.setattr(migrate, "MAX_STATE_BYTES", size)
    assert run(env, dry_run=True).rows_in > 0
    monkeypatch.setattr(migrate, "MAX_STATE_BYTES", size - 1)
    with pytest.raises(migrate.MigrationInputError, match="too large"):
        run(env, dry_run=True)


@pytest.mark.parametrize("actions", [
    [{"id": None}], [{"id": ""}], [{"id": "  "}], [{"id": ["a"]}], [{"id": {"k": 1}}], [{"id": True}], [{"id": 1.5}],
    [{"id": "SECRETID"}, {"id": "SECRETID"}], [{"id": 7}, {"id": "7"}]])
def test_bad_action_item_ids_are_an_input_error_before_any_write(env, actions):
    from school_dashboard.assignments import migrate
    env[2].write_text(json.dumps({"children": {}, "action_items": actions}))
    with pytest.raises(migrate.MigrationInputError) as exc:
        run(env)
    assert "SECRETID" not in str(exc.value)
    assert_no_v2(env[1])


def test_merged_pair_with_both_sides_completed_closes_once_with_the_items_time(tmp_path):
    from datetime import datetime, timezone
    from school_dashboard.assignments.migrate_plan import plan_migration
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    item = {"id": 1, "child": "Alex", "title": "Essay", "due_date": "2026-11-20", "type": "assignment",
            "source": "manual", "completed": 1, "completed_at": "2026-11-17T10:00:00+00:00", "notes": None,
            "created_at": "2026-11-01T10:00:00+00:00"}
    act = {"id": "a1", "child": "Alex", "summary": "Essay", "due": "2026-11-20", "type": "assignment",
           "status": "completed", "completed_at": "2026-11-19T10:00:00+00:00", "created": "2026-11-02T10:00:00+00:00"}
    plan = plan_migration([item], [act], now=now, resolve_kid=lambda n: n)
    assert len(plan.records) == 1 and len(plan.merged) == 1
    state = plan.records[0].state
    assert state.stage == 4 and state.closed_at == datetime.fromisoformat("2026-11-17T10:00:00+00:00")


def test_merged_both_completed_writes_exactly_one_stage_event(env):
    from school_dashboard.assignments import schema
    sdir, dbp, statep = env
    c = sqlite3.connect(dbp)
    c.execute("DELETE FROM items")
    c.execute("INSERT INTO items (id, child, title, due_date, type, source, completed, completed_at, notes, created_at)"
              " VALUES (1, 'Alex', 'Essay', '2026-11-20', 'assignment', 'manual', 1, '2026-11-17T10:00:00-05:00', NULL,"
              " '2026-11-01T10:00:00-05:00')")
    c.commit()
    c.close()
    statep.write_text(json.dumps({"children": {}, "action_items": [{
        "id": "a1", "child": "Alex", "summary": "Essay", "due": "2026-11-20", "type": "assignment",
        "status": "completed", "completed_at": "2026-11-19T10:00:00-05:00", "created": "2026-11-02T10:00:00-05:00"}]}))
    run(env)
    conn = schema.connect(dbp, readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 1
    events = conn.execute("SELECT kind FROM assignment_events").fetchall()
    closed = conn.execute("SELECT closed_at FROM assignments").fetchone()[0]
    conn.close()
    assert [e[0] for e in events] == ["stage"] and closed.startswith("2026-11-17T10:00:00")
