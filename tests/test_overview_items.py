from datetime import date, datetime
from zoneinfo import ZoneInfo

from school_dashboard.overview_items import (
    build_items,
    build_items_report,
    classify,
    local_today,
    next_school_day,
)

NY = ZoneInfo("America/New_York")
KIDS = ["Alpha", "Beta"]


def _ny(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=NY)


def test_local_today_rolls_over_at_0400():
    assert local_today(_ny(2026, 11, 7, 3, 59), NY) == date(2026, 11, 6)
    assert local_today(_ny(2026, 11, 7, 4, 1), NY) == date(2026, 11, 7)


def test_next_school_day_rolls_weekends():
    mon = date(2026, 11, 9)
    assert next_school_day(date(2026, 11, 6)) == mon
    assert next_school_day(date(2026, 11, 7)) == mon
    assert next_school_day(date(2026, 11, 8)) == mon
    assert next_school_day(mon) == date(2026, 11, 10)


def test_local_today_dst_days():
    # spring forward 2027-03-14, fall back 2026-11-01
    assert local_today(_ny(2027, 3, 14, 3, 30), NY) == date(2027, 3, 13)
    assert local_today(_ny(2027, 3, 14, 5, 0), NY) == date(2027, 3, 14)
    assert local_today(_ny(2026, 11, 1, 3, 30), NY) == date(2026, 10, 31)
    assert local_today(_ny(2026, 11, 1, 5, 0), NY) == date(2026, 11, 1)


def test_classify_rules():
    today, nxt = date(2026, 11, 6), date(2026, 11, 9)  # Friday, Monday
    assert classify("2026-11-05", today, nxt) == "overdue"
    assert classify("2026-11-06", today, nxt) == "tonight"
    assert classify("2026-11-09", today, nxt) == "tonight"
    assert classify("2026-11-10", today, nxt) == "upcoming"
    assert classify(None, today, nxt) is None
    assert classify("2026-11-05", today, nxt, done=True) is None
    assert classify("2026-01-01", today, nxt, flag="missing") == "missing"
    assert classify(None, today, nxt, flag="late") == "late"


def test_ixl_row_is_readonly_tonight_while_remaining():
    today, nxt = date(2026, 11, 6), date(2026, 11, 9)
    state = {"ixl": {"Alpha": {"totals": {"math": {"remaining": 3}, "ela": {"remaining": 0}},
                               "due": "2020-01-01"},
                     "Beta": {"totals": {"math": {"remaining": 0}}}}}
    rows = build_items(state, [], today, KIDS)
    assert len(rows) == 1
    assert rows[0]["source"] == "ixl"
    assert rows[0]["status"] == "tonight"
    assert rows[0]["readonly"] is True
    assert rows[0]["kid"] == "Alpha"


def test_powerschool_and_schoology_not_merged():
    today, nxt = date(2026, 11, 6), date(2026, 11, 9)
    state = {
        "schoology": {"Alpha": {"assignments": [
            {"title": "Essay", "course": "ELA", "due_date": "2026-11-05", "status": ""}]}},
        "powerschool": {"Alpha": {"missing": [
            {"title": "Essay", "course": "ELA", "due": "2026-11-05"}]}},
    }
    rows = build_items(state, [], today, KIDS)
    assert sorted(r["source"] for r in rows) == ["powerschool", "schoology"]
    by_source = {r["source"]: r["status"] for r in rows}
    assert by_source == {"powerschool": "missing", "schoology": "overdue"}


def test_manual_items_and_done_skipped():
    today, nxt = date(2026, 11, 6), date(2026, 11, 9)
    manual = [
        {"id": 1, "child": "Alpha", "title": "Poster", "due_date": "2026-11-05", "completed": 0},
        {"id": 2, "child": "Alpha", "title": "Old", "due_date": "2026-11-01", "completed": 1},
    ]
    rows = build_items({}, manual, today, KIDS)
    assert [(r["title"], r["status"], r["source"]) for r in rows] == [("Poster", "overdue", "manual")]


def test_manual_rows_carry_items_table_id():
    today, nxt = date(2026, 11, 6), date(2026, 11, 9)
    manual = [
        {"id": 7, "child": "Alpha", "title": "Poster", "due_date": "2026-11-05", "completed": 0},
        {"id": "x", "child": "Alpha", "title": "Odd", "due_date": "2026-11-05", "completed": 0},
        {"id": True, "child": "Alpha", "title": "Bool", "due_date": "2026-11-05", "completed": 0},
        {"id": None, "child": "Alpha", "title": "NoId", "due_date": "2026-11-05", "completed": 0},
    ]
    rows = {r["title"]: r for r in build_items({}, manual, today, KIDS)}
    assert rows["Poster"]["itemId"] == 7
    for title in ("Odd", "Bool", "NoId"):  # non-int ids (bool is not a real id) are never exposed
        assert "itemId" not in rows[title]


def test_garbage_dates_skipped():
    today, nxt = date(2026, 11, 6), date(2026, 11, 9)
    bad = ["not-a-date", "", "2026-11-05T10:00:00", None, 12345]
    state = {"schoology": {"Alpha": {"assignments": [
        {"title": f"t{i}", "due_date": d} for i, d in enumerate(bad)]
        + [{"title": "x" * 5000 + "\x00\x1b[31m", "due_date": "2026-11-09"}]}}}
    rows = build_items(state, [], today, KIDS)
    titles = [r["title"] for r in rows]
    assert "t0" not in titles and "t1" not in titles and "t3" not in titles
    long_row = [r for r in rows if r["title"].startswith("xxx")][0]
    assert len(long_row["title"]) <= 160
    assert "\x00" not in long_row["title"] and "\x1b" not in long_row["title"]
    # naive datetime string is accepted by its date part
    assert any(r["title"] == "t2" and r["status"] == "overdue" for r in rows)


def test_caps_at_500():
    today, nxt = date(2026, 11, 6), date(2026, 11, 9)
    state = {"powerschool": {"Alpha": {"missing": [
        {"title": f"m{i}", "due": "2026-10-01"} for i in range(600)]}}}
    rows = build_items(state, [], today, KIDS)
    assert len([r for r in rows if r["status"] == "missing"]) == 500


def test_powerschool_upcoming_past_due_is_never_overdue():
    today = date(2026, 11, 6)
    state = {"powerschool": {"Alpha": {"upcoming": [
        {"title": "Yesterday", "due": "2026-11-05"}, {"title": "Soon", "due": "2026-11-09"}]}}}
    rows = build_items(state, [], today, KIDS)
    assert [(r["title"], r["status"]) for r in rows] == [("Soon", "tonight")]


def _missing(n):
    return {"powerschool": {"Alpha": {"missing": [
        {"title": f"m{i}", "due": f"2026-09-{(i % 28) + 1:02d}"} for i in range(n)]}}}


def test_dropped_counts_exact_500_vs_501():
    today = date(2026, 11, 6)
    _, dropped = build_items_report(_missing(500), [], today, KIDS)
    assert dropped == {"overdue": 0, "missing": 0}
    rows, dropped = build_items_report(_missing(501), [], today, KIDS)
    assert dropped["missing"] == 1 and len(rows) == 500


def test_cap_keeps_most_urgent_rows():
    today = date(2026, 11, 6)
    state = {"powerschool": {"Alpha": {"missing": (
        [{"title": f"new{i}", "due": "2026-11-05"} for i in range(500)]
        + [{"title": "oldest", "due": "2026-01-01"}])}}}
    rows = build_items(state, [], today, KIDS)
    assert len(rows) == 500
    assert "oldest" in [r["title"] for r in rows]


def test_url_scheme_allowlist():
    today = date(2026, 11, 6)
    state = {"schoology": {"Alpha": {"assignments": [
        {"title": "a", "due_date": "2026-11-09", "link": "javascript:alert(1)"},
        {"title": "b", "due_date": "2026-11-09", "link": "https://example.test/x"},
        {"title": "c", "due_date": "2026-11-09", "link": "data:text/html,hi"}]}}}
    urls = {r["title"]: r["url"] for r in build_items(state, [], today, KIDS)}
    assert urls == {"a": "", "b": "https://example.test/x", "c": ""}


def test_item_ids_stable_across_list_order():
    today = date(2026, 11, 6)
    a = {"title": "A", "due_date": "2026-11-09"}
    b = {"title": "B", "due_date": "2026-11-09"}
    one = {r["title"]: r["id"] for r in build_items(
        {"schoology": {"Alpha": {"assignments": [a, b]}}}, [], today, KIDS)}
    two = {r["title"]: r["id"] for r in build_items(
        {"schoology": {"Alpha": {"assignments": [b, a]}}}, [], today, KIDS)}
    assert one == two and one["A"] != one["B"]
