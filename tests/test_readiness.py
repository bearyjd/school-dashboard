import json
import sqlite3
from datetime import date, timedelta

import pytest

from school_dashboard.readiness import get_checklist, format_checklist_text


@pytest.fixture
def tmp_state(tmp_path):
    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    overdue = (today - timedelta(days=1)).isoformat()
    far = (today + timedelta(days=10)).isoformat()
    state = {
        "schoology": {
            "Jack": {
                "assignments": [
                    {"title": "Math HW", "due_date": tomorrow, "course": "Math", "status": "not submitted"},
                    {"title": "Old Essay", "due_date": overdue, "course": "English", "status": "not submitted"},
                    {"title": "Done HW", "due_date": tomorrow, "course": "Science", "status": "submitted"},
                    {"title": "Far HW", "due_date": far, "course": "History", "status": "not submitted"},
                ]
            }
        },
        "ixl": {
            "Jack": {
                "totals": {
                    "math": {"remaining": 3, "assigned": 5, "done": 2},
                    "ela": {"remaining": 0, "assigned": 2, "done": 2},
                }
            }
        },
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state))
    return str(p)


@pytest.fixture
def tmp_db(tmp_path):
    db = tmp_path / "school.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, date TEXT, title TEXT, type TEXT, child TEXT)"
    )
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO events VALUES (1, ?, 'Science Test', 'TEST', '')", (tomorrow,))
    conn.execute("INSERT INTO events VALUES (2, ?, 'Reading Quiz', 'QUIZ', 'Jack')", (tomorrow,))
    conn.commit()
    conn.close()
    return str(db)


def test_overdue_assignment_urgency(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    jack = result["Jack"]
    overdue_items = [i for i in jack if i["label"] == "Old Essay"]
    assert len(overdue_items) == 1
    assert overdue_items[0]["urgency"] == "overdue"
    assert overdue_items[0]["type"] == "assignment"


def test_tomorrow_assignment_urgency(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    jack = result["Jack"]
    tomorrow_items = [i for i in jack if i["label"] == "Math HW"]
    assert len(tomorrow_items) == 1
    assert tomorrow_items[0]["urgency"] == "tomorrow"


def test_submitted_assignment_excluded(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    labels = [i["label"] for i in result.get("Jack", [])]
    assert "Done HW" not in labels


def test_beyond_cutoff_excluded(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db, days_ahead=3)
    labels = [i["label"] for i in result.get("Jack", [])]
    assert "Far HW" not in labels


def test_ixl_remaining_included(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    jack = result["Jack"]
    ixl_items = [i for i in jack if i["type"] == "ixl"]
    assert len(ixl_items) == 1  # ela has 0 remaining — excluded
    assert "3" in ixl_items[0]["label"]
    assert ixl_items[0]["urgency"] == "pending"


def test_test_event_included(tmp_state, tmp_db):
    result = get_checklist(tmp_state, tmp_db)
    jack = result["Jack"]
    test_items = [i for i in jack if i["type"] == "test"]
    titles = [i["label"] for i in test_items]
    assert "Science Test" in titles
    assert "Reading Quiz" in titles


def test_format_checklist_text(tmp_state, tmp_db):
    checklist = get_checklist(tmp_state, tmp_db)
    text = format_checklist_text(checklist)
    assert "Jack" in text
    assert "Old Essay" in text
    assert "[overdue]" in text.lower()


def test_empty_state_returns_empty(tmp_path, tmp_db):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({}))
    result = get_checklist(str(empty), tmp_db)
    assert result == {}
