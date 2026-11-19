"""schooldays.py: argument validation not covered by the S0 tests."""
from datetime import date

import pytest

from school_dashboard.assignments import schooldays as sd


def test_add_school_days_rejects_negative_n():
    with pytest.raises(ValueError):
        sd.add_school_days(date(2026, 11, 20), -1, frozenset())
