import sqlite3
from pathlib import Path

import pytest

from school_dashboard import paths

ENV_VARS = [
    "SCHOOL_STATE_DIR", "SCHOOL_STATE_PATH", "SCHOOL_DB_PATH", "SCHOOL_FACTS_PATH",
    "SCHOOL_EMAIL_DIGEST", "SCHOOL_GC_PATH", "SCHOOL_SYNC_META_PATH",
    "SCHOOL_DASHBOARD_HTML", "SCHOOL_CALENDAR_PDF",
]

CASES = [
    ("state_path", "SCHOOL_STATE_PATH", "school-state.json"),
    ("db_path", "SCHOOL_DB_PATH", "school.db"),
    ("facts_path", "SCHOOL_FACTS_PATH", "facts.json"),
    ("email_digest_path", "SCHOOL_EMAIL_DIGEST", "email-digest.json"),
    ("gc_path", "SCHOOL_GC_PATH", "gc-schedule.json"),
    ("sync_meta_path", "SCHOOL_SYNC_META_PATH", "sync_meta.json"),
    ("dashboard_html_path", "SCHOOL_DASHBOARD_HTML", "school-dashboard.html"),
    ("calendar_pdf_path", "SCHOOL_CALENDAR_PDF", "calendar.pdf"),
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in ENV_VARS:
        monkeypatch.delenv(v, raising=False)


def test_state_dir_default():
    assert paths.state_dir() == Path("/app/state")


def test_state_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path))
    assert paths.state_dir() == tmp_path


@pytest.mark.parametrize("fn,env,name", CASES)
def test_default(fn, env, name):
    assert getattr(paths, fn)() == Path("/app/state") / name


@pytest.mark.parametrize("fn,env,name", CASES)
def test_env_override(fn, env, name, monkeypatch, tmp_path):
    monkeypatch.setenv(env, str(tmp_path / "x"))
    assert getattr(paths, fn)() == tmp_path / "x"


@pytest.mark.parametrize("fn,env,name", CASES)
def test_state_dir_override(fn, env, name, monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path))
    assert getattr(paths, fn)() == tmp_path / name


@pytest.mark.parametrize("fn,env,name", CASES)
def test_specific_env_beats_state_dir(fn, env, name, monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path / "dir"))
    monkeypatch.setenv(env, str(tmp_path / "x"))
    assert getattr(paths, fn)() == tmp_path / "x"


@pytest.mark.parametrize("fn,env,name", CASES)
def test_per_call_reresolution(fn, env, name, monkeypatch, tmp_path):
    monkeypatch.setenv(env, str(tmp_path / "a"))
    first = getattr(paths, fn)()
    monkeypatch.setenv(env, str(tmp_path / "b"))
    assert first == tmp_path / "a"
    assert getattr(paths, fn)() == tmp_path / "b"


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
    conn.commit()
    conn.close()


def test_readiness_honors_db_env_set_after_import(monkeypatch, tmp_path):
    from web.app import app
    db = tmp_path / "late.db"
    _make_db(db)
    monkeypatch.setenv("SCHOOL_DB_PATH", str(db))
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "state.json"))
    seen = {}

    def fake(state, dbp):
        seen["db"] = str(dbp)
        return {}

    monkeypatch.setattr("school_dashboard.readiness.get_checklist", fake)
    resp = app.test_client().get("/api/readiness")
    assert resp.status_code == 200
    assert seen["db"] == str(db)


def test_digest_get_honors_db_env_set_after_import(monkeypatch, tmp_path):
    from web.app import app
    db = tmp_path / "late.db"
    _make_db(db)
    monkeypatch.setenv("SCHOOL_DB_PATH", str(db))
    resp = app.test_client().get("/api/digest/nope")
    assert resp.status_code == 404
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("select name from sqlite_master")}
    conn.close()
    assert "digests" in tables


def test_state_dir_follows_state_path_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "sub" / "s.json"))
    assert paths.state_dir() == tmp_path / "sub"


def test_state_dir_env_beats_state_path(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "sub" / "s.json"))
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path / "dir"))
    assert paths.state_dir() == tmp_path / "dir"
    assert paths.email_digest_path() == tmp_path / "dir" / "email-digest.json"


def test_email_digest_and_dashboard_follow_state_path(monkeypatch, tmp_path):
    from school_dashboard import email, html
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "s.json"))
    assert email._digest_path() == tmp_path / "email-digest.json"
    assert paths.dashboard_html_path() == tmp_path / "school-dashboard.html"
    out = html.render({"children": {}})
    assert out == tmp_path / "school-dashboard.html"
    assert out.exists()
