from datetime import date, timedelta
from school_dashboard.html import _urgency, _build_urgent_items


def _due(delta_days):
    """Return ISO date string offset from today."""
    return (date.today() + timedelta(days=delta_days)).isoformat() + "T23:59:00"


def test_urgency_overdue():
    assert _urgency(_due(-1)) == "overdue"


def test_urgency_today():
    assert _urgency(_due(0)) == "today"


def test_urgency_tomorrow():
    assert _urgency(_due(1)) == "tomorrow"


def test_urgency_upcoming():
    assert _urgency(_due(3)) == "upcoming"


def test_urgency_no_date():
    assert _urgency(None) == "upcoming"
    assert _urgency("") == "upcoming"


def test_build_urgent_items_filters_and_sorts():
    children = [
        {
            "name": "Alice",
            "assignments": [
                {"title": "Late essay", "course": "ELA", "due_date": _due(-2)},
                {"title": "Future project", "course": "Science", "due_date": _due(5)},
                {"title": "Due today", "course": "Math", "due_date": _due(0)},
            ],
        },
        {
            "name": "Bob",
            "assignments": [
                {"title": "Tomorrow quiz", "course": "History", "due_date": _due(1)},
            ],
        },
    ]
    urgent = _build_urgent_items(children)
    # Only overdue, today, tomorrow — not the 5-day future item
    assert len(urgent) == 3
    # Sorted: overdue first, then today, then tomorrow
    assert urgent[0]["urgency"] == "overdue"
    assert urgent[1]["urgency"] == "today"
    assert urgent[2]["urgency"] == "tomorrow"
    # Child name attached
    assert urgent[0]["child"] == "Alice"


def test_embedded_state_json_cannot_break_out_of_script(tmp_path):
    """A scraped title with </script> must not end the state-json block (same origin as /app/)."""
    import json
    import re
    from school_dashboard.html import render

    evil = '</script><script>alert(1)</script> & <!--    "q"'
    state = {
        "last_updated": "now",
        "children": {"Kid": {"grade": "5"}},
        "ixl": {},
        "action_items": [{"title": evil, "status": "pending", "child": "Kid"}],
        "schoology": {"Kid": {"assignments": [{"title": evil, "course": "C", "due_date": None}], "grades": []}},
    }
    page = render(state, str(tmp_path / "d.html")).read_text()
    assert "<script>alert(1)" not in page
    m = re.search(r'<script id="state-json" type="application/json">(.*?)</script>', page, re.S)
    assert m, "state-json block missing"
    block = m.group(1)
    assert "<" not in block and ">" not in block and "&" not in block
    assert " " not in block and " " not in block
    assert json.loads(block) == state
