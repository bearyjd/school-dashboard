"""Tests for school_dashboard.digest"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from school_dashboard.digest import (
    build_morning_digest,
    build_afternoon_digest,
    build_night_digest,
    send_ntfy,
    _load_state,
    _load_facts,
)


@pytest.fixture
def tmp_state(tmp_path):
    state = {
        "schoology": {
            "Jack": {
                "assignments": [
                    {"title": "Math HW", "due_date": "2026-04-10", "course": "Math", "status": ""},
                    {"title": "Future HW", "due_date": "2026-04-11", "course": "ELA", "status": ""},
                ]
            }
        },
        "ixl": {
            "Jack": {"totals": {"Math": {"remaining": 3, "assigned": 5, "done": 2}}}
        },
        "action_items": [
            {"child": "Jack", "source": "email", "summary": "Return field trip form", "due": "2026-04-10"},
        ],
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state))
    return str(p)


@pytest.fixture
def tmp_facts(tmp_path):
    facts = [{"subject": "Jack", "fact": "Soccer practice on Tuesdays"}]
    p = tmp_path / "facts.json"
    p.write_text(json.dumps(facts))
    return str(p)


@pytest.fixture
def tmp_db(tmp_path):
    import sqlite3
    db = tmp_path / "school.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            date TEXT,
            title TEXT,
            type TEXT,
            child TEXT
        )
    """)
    conn.execute("INSERT INTO events VALUES (1, '2026-04-10', 'Mass', 'MASS', '')")
    conn.execute("INSERT INTO events VALUES (2, '2026-04-11', 'No School', 'NO_SCHOOL', '')")
    conn.commit()
    conn.close()
    return str(db)


def test_load_state(tmp_state):
    state = _load_state(tmp_state)
    assert "schoology" in state
    assert "Jack" in state["schoology"]


def test_load_facts(tmp_facts):
    facts = _load_facts(tmp_facts)
    assert facts[0]["fact"] == "Soccer practice on Tuesdays"


def test_load_facts_missing():
    facts = _load_facts("/nonexistent/facts.json")
    assert facts == []


@patch("school_dashboard.digest.requests.post")
def test_send_ntfy(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    send_ntfy(topic="test-topic", message="Hello", title="Test")
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "test-topic" in call_kwargs[0][0]


@patch("school_dashboard.digest.requests.post")
def test_build_morning_digest_calls_litellm(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Good morning!"}}]},
    )
    result = build_morning_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        gcal_events=[],
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        today="2026-04-10",
    )
    assert "Good morning!" in result
    mock_post.assert_called_once()


@patch("school_dashboard.digest.requests.post")
def test_build_afternoon_digest_calls_litellm(mock_post, tmp_state):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Homework check!"}}]},
    )
    result = build_afternoon_digest(
        state_path=tmp_state,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        today="2026-04-10",
    )
    assert "Homework check!" in result


@patch("school_dashboard.digest.requests.post")
def test_build_night_digest_calls_litellm(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Ready for tomorrow!"}}]},
    )
    result = build_night_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        gcal_events=[],
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        tomorrow="2026-04-11",
    )
    assert "Ready for tomorrow!" in result
