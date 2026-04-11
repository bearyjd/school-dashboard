"""Tests for school_dashboard.digest"""
import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from school_dashboard.digest import (
    build_morning_digest,
    build_afternoon_digest,
    build_night_digest,
    build_weekly_digest,
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
    result, cards = build_morning_digest(
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
    assert isinstance(cards, list)
    mock_post.assert_called_once()


@patch("school_dashboard.digest.requests.post")
def test_build_afternoon_digest_calls_litellm(mock_post, tmp_state):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Homework check!"}}]},
    )
    result, cards = build_afternoon_digest(
        state_path=tmp_state,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        today="2026-04-10",
    )
    assert "Homework check!" in result
    assert isinstance(cards, list)


@patch("school_dashboard.digest.requests.post")
def test_build_night_digest_calls_litellm(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Ready for tomorrow!"}}]},
    )
    result, cards = build_night_digest(
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
    assert isinstance(cards, list)


@pytest.fixture
def tmp_state_with_items(tmp_path):
    """State with assignments due within 3 days and IXL remaining > 0."""
    today = date.today()
    due_tomorrow = (today + timedelta(days=1)).isoformat()
    due_soon = (today + timedelta(days=2)).isoformat()
    state = {
        "schoology": {
            "Jack": {
                "assignments": [
                    {"title": "Math HW", "due_date": due_tomorrow, "course": "Math", "status": ""},
                    {"title": "Science HW", "due_date": due_soon, "course": "Science", "status": ""},
                ]
            }
        },
        "ixl": {
            "Jack": {"totals": {"Math": {"remaining": 3, "assigned": 5, "done": 2}}}
        },
        "action_items": [],
    }
    p = tmp_path / "state_with_items.json"
    p.write_text(json.dumps(state))
    return str(p)


@patch("school_dashboard.digest.requests.post")
def test_afternoon_digest_includes_checklist(mock_post, tmp_state_with_items, tmp_db):
    """Checklist section appended after LiteLLM response."""
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Homework check done."}}]},
    )
    from school_dashboard.digest import build_afternoon_digest
    result, cards = build_afternoon_digest(
        state_path=tmp_state_with_items,
        db_path=tmp_db,
        litellm_url="http://fake-llm",
        api_key="key",
        model="gpt-4",
    )
    assert "Homework check done." in result
    assert "Action items:" in result
    assert isinstance(cards, list)


@patch("school_dashboard.digest.requests.post")
def test_night_digest_includes_checklist(mock_post, tmp_state_with_items, tmp_facts, tmp_db):
    """Night digest appends checklist with 'Before bed' prefix."""
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Night prep ready."}}]},
    )
    from school_dashboard.digest import build_night_digest
    result, cards = build_night_digest(
        state_path=tmp_state_with_items,
        db_path=tmp_db,
        facts_path="/dev/null",
        gcal_events=[],
        litellm_url="http://fake-llm",
        api_key="key",
        model="gpt-4",
    )
    assert "Night prep ready." in result
    assert "Before bed" in result
    assert isinstance(cards, list)


@patch("school_dashboard.digest.requests.post")
def test_weekly_digest_friday_builds_text(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Week in review!"}}]},
    )
    result, cards = build_weekly_digest(
        mode="friday",
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
    )
    assert result == "Week in review!"
    assert isinstance(cards, list)
    mock_post.assert_called_once()


@patch("school_dashboard.digest.requests.post")
def test_weekly_digest_sunday_builds_text(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Week ahead!"}}]},
    )
    result, cards = build_weekly_digest(
        mode="sunday",
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
    )
    assert result == "Week ahead!"
    assert isinstance(cards, list)
    mock_post.assert_called_once()


def test_weekly_digest_empty_state(tmp_path, tmp_facts, tmp_db):
    """Missing state file returns graceful string, not an exception."""
    missing = str(tmp_path / "nonexistent.json")
    with patch("school_dashboard.digest.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "OK"}}]},
        )
        result, cards = build_weekly_digest(
            mode="friday",
            state_path=missing,
            db_path=tmp_db,
            facts_path=tmp_facts,
            litellm_url="http://localhost:4000",
            api_key="test-key",
            model="claude-sonnet",
        )
    assert isinstance(result, str)
    assert len(result) > 0
    assert isinstance(cards, list)


@patch("school_dashboard.digest.requests.post")
def test_morning_digest_returns_cards(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Good morning!"}}]},
    )
    text, cards = build_morning_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        gcal_events=[{"title": "Soccer", "start": "2026-04-10T16:00", "location": "Field"}],
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        today="2026-04-10",
    )
    assert text == "Good morning!"
    assert isinstance(cards, list)
    assert len(cards) >= 1
    sources = {c["source"] for c in cards}
    assert "schoology" in sources or "ixl" in sources or "calendar" in sources


@patch("school_dashboard.digest.requests.post")
def test_send_ntfy_with_cards_creates_digest(mock_post, tmp_path):
    mock_post.return_value = MagicMock(ok=True, status_code=200)
    db = tmp_path / "school.db"
    from school_dashboard.db import init_digests_table, get_digest
    init_digests_table(str(db))

    cards = [
        {"source": "schoology", "child": "Ford", "title": "Math HW", "detail": "Pre-Algebra",
         "due_date": "2026-04-11", "url": "", "done": False},
    ]
    send_ntfy(topic="test-topic", message="Hello", title="Homework Check",
              cards=cards, db_path=str(db))

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    assert "digest=" in headers.get("Click", "")
    assert "school.grepon.cc" in headers.get("Click", "")
    # Verify the digest was actually persisted to DB
    click_url = headers.get("Click", "")
    digest_id = click_url.split("digest=")[1]
    result = get_digest(str(db), digest_id)
    assert result is not None
    assert result["cards"][0]["title"] == "Math HW"


@patch("school_dashboard.digest.requests.post")
def test_send_ntfy_without_cards_uses_static_links(mock_post):
    mock_post.return_value = MagicMock(ok=True, status_code=200)
    send_ntfy(topic="test-topic", message="Hello", title="Homework Check")
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    # Should use static deep link (mode=schoology)
    assert "mode=schoology" in headers.get("Click", "")


# --- Digest DB tests ---

from school_dashboard.db import (
    create_digest,
    get_digest,
    mark_digest_card_done,
    purge_old_digests,
)


@pytest.fixture
def digest_db(tmp_path):
    """SQLite DB with digests table initialized."""
    db = tmp_path / "digest.db"
    from school_dashboard.db import init_digests_table
    init_digests_table(str(db))
    return str(db)


def test_create_and_get_digest(digest_db):
    cards = [
        {"source": "schoology", "child": "Ford", "title": "Math HW", "detail": "Pre-Algebra",
         "due_date": "2026-04-11", "url": "", "done": False},
        {"source": "ixl", "child": "Jack", "title": "Math", "detail": "3 remaining",
         "due_date": None, "url": "", "done": False},
    ]
    digest_id = create_digest(digest_db, "Morning Briefing", cards)
    assert len(digest_id) == 8
    result = get_digest(digest_db, digest_id)
    assert result is not None
    assert result["title"] == "Morning Briefing"
    assert len(result["cards"]) == 2
    assert result["cards"][0]["child"] == "Ford"
    assert result["cards"][1]["source"] == "ixl"


def test_get_digest_not_found(digest_db):
    assert get_digest(digest_db, "nonexist") is None


def test_mark_digest_card_done(digest_db):
    cards = [
        {"source": "schoology", "child": "Ford", "title": "Math HW", "detail": "",
         "due_date": None, "url": "", "done": False},
    ]
    digest_id = create_digest(digest_db, "Test", cards)
    assert mark_digest_card_done(digest_db, digest_id, 0, True) is True
    result = get_digest(digest_db, digest_id)
    assert result["cards"][0]["done"] is True


def test_mark_digest_card_done_invalid_index(digest_db):
    cards = [{"source": "ixl", "child": "Ford", "title": "X", "detail": "",
              "due_date": None, "url": "", "done": False}]
    digest_id = create_digest(digest_db, "Test", cards)
    assert mark_digest_card_done(digest_db, digest_id, 99, True) is False


def test_purge_old_digests(digest_db):
    cards = [{"source": "ixl", "child": "Ford", "title": "X", "detail": "",
              "due_date": None, "url": "", "done": False}]
    digest_id = create_digest(digest_db, "Old", cards)
    # Manually backdate the row
    import sqlite3
    conn = sqlite3.connect(digest_db)
    conn.execute("UPDATE digests SET created_at = '2020-01-01T00:00:00'")
    conn.commit()
    conn.close()
    deleted = purge_old_digests(digest_db, days=7)
    assert deleted == 1
    assert get_digest(digest_db, digest_id) is None
