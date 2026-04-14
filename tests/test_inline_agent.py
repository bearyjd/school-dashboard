"""Tests for /api/agent/inline endpoint."""
import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from web.app import app as flask_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "school.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            child TEXT NOT NULL,
            title TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'assignment',
            source TEXT NOT NULL DEFAULT 'manual',
            due_date TEXT,
            notes TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO items (id, child, title, type, source, due_date, completed)"
        " VALUES (1, 'Ford', 'Math HW', 'assignment', 'sgy', '2026-04-15', 0)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SCHOOL_DB_PATH", str(db))
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(tmp_path / "sync_meta.json"))
    monkeypatch.setenv("LITELLM_URL", "http://mock-litellm:8080")
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _mock_litellm(reply: str) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status = lambda: None
    mock.json.return_value = {"choices": [{"message": {"content": reply}}]}
    return mock


def test_inline_agent_missing_fields_returns_400(client):
    r = client.post("/api/agent/inline", json={"context_type": "item"})
    assert r.status_code == 400
    assert "required" in r.get_json()["error"]


def test_inline_agent_unknown_context_type_returns_400(client):
    r = client.post("/api/agent/inline", json={
        "context_type": "bogus", "context_id": "1", "message": "hi"
    })
    assert r.status_code == 400


def test_inline_agent_item_reply_no_action(client):
    with patch("requests.post", return_value=_mock_litellm("It looks done already.")):
        r = client.post("/api/agent/inline", json={
            "context_type": "item", "context_id": "1", "message": "Is this done?"
        })
    assert r.status_code == 200
    body = r.get_json()
    assert body["reply"] == "It looks done already."
    assert body["action"] is None


def test_inline_agent_item_reply_with_action(client):
    reply = 'Sure, marking it done.\nACTION: mark_item_done {"id": 1}'
    with patch("requests.post", return_value=_mock_litellm(reply)):
        r = client.post("/api/agent/inline", json={
            "context_type": "item", "context_id": "1", "message": "Mark it done"
        })
    assert r.status_code == 200
    body = r.get_json()
    assert "marking it done" in body["reply"]
    assert body["action"] == {"type": "mark_item_done", "payload": {"id": 1}}
    assert "ACTION:" not in body["reply"]


def test_inline_agent_sync_source(client, tmp_path, monkeypatch):
    meta_path = tmp_path / "sync_meta.json"
    meta_path.write_text(json.dumps({
        "ixl": {"last_run": "2026-04-13T06:00:00", "last_result": "ok"}
    }))
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(meta_path))
    with patch("requests.post", return_value=_mock_litellm("IXL synced this morning.")):
        r = client.post("/api/agent/inline", json={
            "context_type": "sync_source", "context_id": "ixl", "message": "Status?"
        })
    assert r.status_code == 200
    assert "IXL" in r.get_json()["reply"]
