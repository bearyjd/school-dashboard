"""schooldays.py: 04:00 rollover, calendar rows, school-day arithmetic."""
from datetime import date

import pytest

from recon.support import local
from school_dashboard.assignments import schooldays as sd

# 2026-11-20 is a Friday.
FRI, SAT, SUN, MON, TUE, WED = (date(2026, 11, n) for n in (20, 21, 22, 23, 24, 25))


def test_school_today_requires_tz_aware_now():
    from datetime import datetime
    with pytest.raises(ValueError):
        sd.school_today(datetime(2026, 11, 18, 12, 0))


@pytest.mark.parametrize("now,expected", [
    (local(2026, 11, 18, 3, 59), date(2026, 11, 17)),
    (local(2026, 11, 18, 4, 0), date(2026, 11, 18)),
    (local(2026, 11, 18, 23, 59), date(2026, 11, 18)),
    (local(2026, 11, 18, 0, 0), date(2026, 11, 17)),
    # Spring forward 2026-03-08 (02:00 -> 03:00): wall-clock 04:00 rollover must hold.
    (local(2026, 3, 8, 3, 30), date(2026, 3, 7)),
    (local(2026, 3, 8, 4, 30), date(2026, 3, 8)),
    # Fall back 2026-11-01 (02:00 -> 01:00).
    (local(2026, 11, 1, 3, 30), date(2026, 10, 31)),
    (local(2026, 11, 1, 4, 30), date(2026, 11, 1)),
])
def test_school_today_uses_wall_clock_0400_rollover(now, expected):
    assert sd.school_today(now) == expected


def _row(day, type_="NO_SCHOOL", child=None):
    return {"date": day, "title": "x", "type": type_, "child": child}


def test_no_school_dates_null_child_applies_to_everyone():
    assert sd.no_school_dates([_row("2026-11-23")], "Alex") == frozenset({MON})


@pytest.mark.parametrize("child", ["", "   "])
def test_no_school_dates_blank_child_applies_to_everyone(child):
    assert sd.no_school_dates([_row("2026-11-23", child=child)], "Sam") == frozenset({MON})


def test_no_school_dates_comma_joined_child_matches_each_name():
    rows = [_row("2026-11-23", child="Alex, Sam")]
    assert sd.no_school_dates(rows, "Sam") == frozenset({MON})


def test_no_school_dates_comma_joined_child_excludes_unlisted_kid():
    assert sd.no_school_dates([_row("2026-11-23", child="Alex, Sam")], "Jo") == frozenset()


def test_no_school_dates_compares_casefolded_and_trimmed():
    assert sd.no_school_dates([_row("2026-11-23", child="  ALEX ,sam")], "alex") == frozenset({MON})


def test_no_school_dates_does_not_match_name_substrings():
    assert sd.no_school_dates([_row("2026-11-23", child="Alexander")], "Alex") == frozenset()


def test_no_school_dates_ignores_other_event_types():
    assert sd.no_school_dates([_row("2026-11-23", type_="TEST")], "Alex") == frozenset()


def test_no_school_dates_returns_frozenset_of_dates():
    assert isinstance(sd.no_school_dates([], "Alex"), frozenset)


def test_calendar_covers_true_when_row_on_today():
    assert sd.calendar_covers([_row("2026-11-20", type_="TEST")], FRI) is True


def test_calendar_covers_true_when_row_after_today():
    assert sd.calendar_covers([_row("2026-12-01")], FRI) is True


def test_calendar_covers_false_when_only_past_rows():
    assert sd.calendar_covers([_row("2026-11-19")], FRI) is False


def test_calendar_covers_false_when_no_rows():
    assert sd.calendar_covers([], FRI) is False


@pytest.mark.parametrize("day,expected", [(FRI, True), (SAT, False), (SUN, False), (MON, True)])
def test_is_school_day_excludes_weekends(day, expected):
    assert sd.is_school_day(day, frozenset()) is expected


def test_is_school_day_false_on_no_school_date():
    assert sd.is_school_day(MON, frozenset({MON})) is False


@pytest.mark.parametrize("start,no_school,expected", [
    (date(2026, 11, 18), frozenset(), date(2026, 11, 19)),
    (FRI, frozenset(), MON),
    (SAT, frozenset(), MON),
    (FRI, frozenset({MON}), TUE),
    (MON, frozenset(), TUE),
    (date(2026, 11, 19), frozenset({FRI}), MON),
])
def test_next_school_day_is_strictly_after(start, no_school, expected):
    assert sd.next_school_day(start, no_school) == expected


@pytest.mark.parametrize("start,n,no_school,expected", [
    (FRI, 0, frozenset(), FRI),
    (FRI, 1, frozenset(), MON),
    (FRI, 2, frozenset(), TUE),
    (date(2026, 11, 18), 2, frozenset(), FRI),
    (date(2026, 11, 18), 2, frozenset({date(2026, 11, 19), FRI}), TUE),
    (FRI, 3, frozenset({MON}), date(2026, 11, 26)),
])
def test_add_school_days_skips_weekends_and_no_school_dates(start, n, no_school, expected):
    assert sd.add_school_days(start, n, no_school) == expected


def _alias(name: str) -> str:
    return {"samantha": "Sam"}.get(name.strip().lower(), name.strip().title())


@pytest.mark.parametrize("child", ["Samantha", "samantha, Alex", "Sam"])
def test_no_school_dates_resolves_aliases_on_row_and_kid(child):
    assert sd.no_school_dates([_row("2026-11-23", child=child)], "Sam", resolve=_alias) == frozenset({MON})
    assert sd.no_school_dates([_row("2026-11-23", child=child)], "samantha", resolve=_alias) == frozenset({MON})


def test_no_school_dates_resolve_keeps_other_kids_and_null_rows():
    assert sd.no_school_dates([_row("2026-11-23", child="Alex")], "Sam", resolve=_alias) == frozenset()
    assert sd.no_school_dates([_row("2026-11-23", child=None)], "Sam", resolve=_alias) == frozenset({MON})


def test_no_school_dates_without_resolve_does_not_alias():
    assert sd.no_school_dates([_row("2026-11-23", child="Samantha")], "Sam") == frozenset()
