import json
import sqlite3
from datetime import date, timedelta

import pytest

from school_dashboard import db as db_module
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


def test_init_db_creates_events_table_and_checklist_surfaces_test_event(tmp_state, tmp_path):
    """Regression test for the events-table bug (agent_roadmap.md item 3).

    `calendar_import.py` used to create + populate the SQLite `events` table but
    has been removed from the codebase. Nothing else creates that table, so a
    real `school.db` produced via the normal `db.init_db()` init path never gets
    an `events` table, and `get_checklist`'s test/quiz query fails against it —
    silently, because it's wrapped in a bare `except Exception`.

    This test exercises the REAL init path (`db.init_db`, the same function the
    app calls to set up a fresh `school.db`), not a hand-crafted table like the
    `tmp_db` fixture above. Before the fix, `db.init_db` does not create an
    `events` table at all, so the INSERT below raises
    `sqlite3.OperationalError: no such table: events` and this test fails
    loudly (proving the bug), rather than passing vacuously.
    """
    db_path = str(tmp_path / "real_school.db")
    db_module.init_db(db_path)

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO events (date, title, type, child) VALUES (?, ?, ?, ?)",
        (tomorrow, "Science Test", "TEST", "Jack"),
    )
    conn.commit()
    conn.close()

    result = get_checklist(tmp_state, db_path)
    jack = result["Jack"]
    test_items = [i for i in jack if i["type"] == "test"]
    assert "Science Test" in [i["label"] for i in test_items]


def test_comma_joined_child_matches_membership(tmp_path):
    state = {"schoology": {n: {"assignments": []} for n in ("Ford", "Jack", "Penn")}}
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps(state))
    dbp = tmp_path / "school.db"
    conn = sqlite3.connect(str(dbp))
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, date TEXT, title TEXT, type TEXT, child TEXT)")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO events VALUES (1, ?, 'Shared Test', 'TEST', 'Ford, Jack')", (tomorrow,))
    conn.commit()
    conn.close()
    result = get_checklist(str(sp), str(dbp))
    has = {c: any(i["label"] == "Shared Test" for i in result.get(c, [])) for c in ("Ford", "Jack", "Penn")}
    assert has == {"Ford": True, "Jack": True, "Penn": False}


def _one_assignment_state(tmp_path, due_date, status="not submitted"):
    p = tmp_path / "one.json"
    p.write_text(json.dumps({"schoology": {"Alpha": {"assignments": [
        {"title": "HW", "due_date": due_date, "course": "Math", "status": status}]}}}))
    return str(p)


def _freeform(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


@pytest.mark.parametrize("make, urgency", [
    (lambda d: d.isoformat(), "tomorrow"),
    (lambda d: d.isoformat() + "T15:30:00", "tomorrow"),
    (lambda d: d.isoformat() + "T15:30:00Z", "tomorrow"),
    (_freeform, "tomorrow"),
    (lambda d: "TBD", None),
    (lambda d: "", None),
])
def test_due_date_formats_are_bucketed_like_overview(tmp_path, monkeypatch, make, urgency):
    monkeypatch.setattr("school_dashboard.readiness.get_roster", lambda: [])
    due = date.today() + timedelta(days=1)
    result = get_checklist(_one_assignment_state(tmp_path, make(due)))
    if urgency is None:
        assert result == {}
    else:
        assert [(i["label"], i["urgency"]) for i in result["Alpha"]] == [("HW", urgency)]


def test_freeform_overdue_date_is_overdue(tmp_path, monkeypatch):
    monkeypatch.setattr("school_dashboard.readiness.get_roster", lambda: [])
    result = get_checklist(_one_assignment_state(tmp_path, _freeform(date.today() - timedelta(days=2))))
    assert result["Alpha"][0]["urgency"] == "overdue"


@pytest.mark.parametrize("status", ["Submitted", "GRADED", "Turned in"])
def test_done_statuses_ignore_case(tmp_path, monkeypatch, status):
    monkeypatch.setattr("school_dashboard.readiness.get_roster", lambda: [])
    assert get_checklist(_one_assignment_state(tmp_path, date.today().isoformat(), status)) == {}


def _ixl_only_state(tmp_path, child):
    p = tmp_path / "ixl.json"
    p.write_text(json.dumps({"ixl": {child: {"totals": {"math": {"remaining": 2}}}}}))
    return str(p)


def test_roster_alias_does_not_create_a_duplicate_checklist(tmp_path, monkeypatch):
    """A roster name that is an alias resolves to the canonical key used by the state."""
    monkeypatch.setattr("school_dashboard.readiness.get_roster", lambda: [{"name": "alpha-alias"}])
    monkeypatch.setattr("school_dashboard.readiness.canonical_name",
                        lambda n: "Alpha" if n == "alpha-alias" else (n or "").strip().title())
    result = get_checklist(_ixl_only_state(tmp_path, "Alpha"))
    assert list(result) == ["Alpha"]


@pytest.mark.parametrize("roster", [[], [{}], [{"name": None}], [{"name": "  "}], [{"name": 5}], ["junk"]])
def test_empty_or_odd_roster_entries_are_skipped(tmp_path, monkeypatch, roster):
    monkeypatch.setattr("school_dashboard.readiness.get_roster", lambda: roster)
    assert get_checklist(_ixl_only_state(tmp_path, "Alpha")) == {}
