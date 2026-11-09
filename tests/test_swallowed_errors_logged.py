"""Fallback paths that used to swallow exceptions now log a warning and still return the fallback."""
import json
import logging
import sqlite3
import sys
from unittest.mock import MagicMock, patch

from school_dashboard import digest, email
from web import app as app_module


def _bad_db(tmp_path):
    db = tmp_path / "school.db"
    db.write_text("not a sqlite database")
    return db


def test_digest_db_error_logs_and_returns_empty(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="school_dashboard.digest"):
        assert digest._query_db_events(str(_bad_db(tmp_path)), "2026-01-01") == []
    assert any("Failed to query digest events" in r.message and r.exc_info for r in caplog.records)


def test_email_pdf_failure_logs_and_returns_empty(caplog):
    boom = MagicMock()
    boom.extract_text.side_effect = RuntimeError("bad pdf")
    with patch.dict(sys.modules, {"pdfminer": MagicMock(), "pdfminer.high_level": boom}):
        with caplog.at_level(logging.WARNING, logger="school_dashboard.email"):
            assert email._extract_pdf_text("/tmp/x.pdf") == ""
    assert any("Failed to extract text from PDF" in r.message and r.exc_info for r in caplog.records)


def test_app_load_upcoming_events_logs_and_returns_empty(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SCHOOL_DB_PATH", str(_bad_db(tmp_path)))
    with caplog.at_level(logging.WARNING):
        assert app_module.load_upcoming_events() == []
    assert any("Failed to load upcoming events" in r.message and r.exc_info for r in caplog.records)


def test_app_load_facts_logs_and_returns_empty(tmp_path, monkeypatch, caplog):
    facts = tmp_path / "facts.json"
    facts.write_text("{ not json")
    monkeypatch.setenv("SCHOOL_FACTS_PATH", str(facts))
    with caplog.at_level(logging.WARNING):
        assert app_module.load_facts() == []
    assert any("Failed to load facts" in r.message and r.exc_info for r in caplog.records)


def test_inline_agent_malformed_action_logs_and_is_ignored(tmp_path, monkeypatch, caplog):
    db = tmp_path / "school.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, child TEXT NOT NULL, title TEXT NOT NULL,"
                 " type TEXT DEFAULT 'assignment', source TEXT DEFAULT 'manual', due_date TEXT, notes TEXT,"
                 " completed INTEGER DEFAULT 0, created_at TEXT)")
    conn.execute("INSERT INTO items (id, child, title) VALUES (1, 'Ford', 'Math HW')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("SCHOOL_DB_PATH", str(db))
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(tmp_path / "m.json"))
    monkeypatch.setenv("LITELLM_URL", "http://mock-litellm:8080")
    monkeypatch.setenv("LITELLM_API_KEY", "k")
    reply = 'Okay.\nACTION: mark_item_done {not json'
    mock = MagicMock(ok=True, status_code=200, text=json.dumps({"choices": [{"message": {"content": reply}}]}),
                     headers={"content-type": "application/json"})
    mock.raise_for_status = lambda: None
    mock.json.return_value = {"choices": [{"message": {"content": reply}}]}
    app_module.app.config["TESTING"] = True
    with patch("school_dashboard.llm.requests.post", return_value=mock), caplog.at_level(logging.WARNING):
        r = app_module.app.test_client().post(
            "/api/agent/inline", json={"context_type": "item", "context_id": "1", "message": "hi"})
    assert r.status_code == 200
    assert r.get_json()["action"] is None
    assert any("malformed ACTION" in rec.message for rec in caplog.records)
    assert not any("not json" in rec.getMessage() for rec in caplog.records)


def _empty_db(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()  # valid SQLite file with no events table
    return db


def test_digest_missing_events_table_is_debug_not_warning(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger="school_dashboard.digest"):
        assert digest._query_db_events(str(_empty_db(tmp_path)), "2026-01-01") == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(r.levelno == logging.DEBUG and "events table" in r.message for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)


def test_app_missing_events_table_is_debug_not_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SCHOOL_DB_PATH", str(_empty_db(tmp_path)))
    with caplog.at_level(logging.DEBUG):
        assert app_module.load_upcoming_events() == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(r.levelno == logging.DEBUG and "Events table" in r.message for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)


def test_collapse_events_tolerates_bad_rows():
    rows = [
        {"title": "Quiz", "type": "QUIZ", "child": 123},
        {"title": "Quiz", "type": "QUIZ", "child": "Ford"},
        {"title": "Test", "type": "TEST", "child": None},
        "garbage",
        {"type": "TEST"},
    ]
    out = digest._collapse_events(rows)
    by_title = {r.get("title"): r for r in out}
    assert by_title["Quiz"]["child"] == "123, Ford"
    assert by_title["Test"]["child"] == ""
    assert len(out) == 3  # garbage skipped; the title-less row is kept with a None title


def test_pdfminer_missing_is_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(email, "_pdfminer_missing_logged", False)
    with patch.dict(sys.modules, {"pdfminer": MagicMock(), "pdfminer.high_level": None}):
        with caplog.at_level(logging.WARNING, logger="school_dashboard.email"):
            assert email._extract_pdf_text("/tmp/a.pdf") == ""
            assert email._extract_pdf_text("/tmp/b.pdf") == ""
    assert len([r for r in caplog.records if "pdfminer is not installed" in r.message]) == 1
