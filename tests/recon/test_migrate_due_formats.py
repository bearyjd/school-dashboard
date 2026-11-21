"""Legacy `due` text formats: parsed when they carry a year, never guessed otherwise."""
from datetime import date

import pytest

from recon.conftest import NOW
from school_dashboard.assignments.migrate_plan import plan_migration


def _plan(due):
    act = {"id": "a1", "child": "Alex", "source": "schoology", "type": "assignment", "summary": "Worksheet",
           "due": due, "status": "pending", "created": "2026-11-01T07:00:00"}
    return plan_migration([], [act], now=NOW, resolve_kid=lambda n: n)


@pytest.mark.parametrize("raw,expected", [
    ("2026-11-16", date(2026, 11, 16)),
    ("2026-11-16T08:00:00-05:00", date(2026, 11, 16)),
    ("11/22/2026", date(2026, 11, 22)),
    ("1/5/2026", date(2026, 1, 5)),
    ("01/05/2026", date(2026, 1, 5)),
    ("1/5/26", date(2026, 1, 5)),
    ("2026/11/16", date(2026, 11, 16)),
    ("Mon, Nov 16, 2026", date(2026, 11, 16)),
    ("Nov 16, 2026", date(2026, 11, 16)),
    ("November 16 2026", date(2026, 11, 16)),
    ("  November 16, 2026 ", date(2026, 11, 16)),
])
def test_parsed_formats(raw, expected):
    plan = _plan(raw)
    assert plan.records[0].state.due_date == expected
    assert not any("unreadable due" in w for w in plan.warnings)


@pytest.mark.parametrize("raw", ["Mon, Nov 16", "Nov 16", "02/30/2026", "13/01/2026", "2026/02/30",
                                 "Nov 31, 2026", "not a date", "Smarch 4, 2026"])
def test_unparsed_formats_stay_undated_and_counted(raw):
    plan = _plan(raw)
    assert plan.records[0].state.due_date is None
    assert any("1 rows had an unreadable due" in w for w in plan.warnings)
