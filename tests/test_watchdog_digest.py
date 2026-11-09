"""Tests for the watchdog's missed-digest rule (check_digest_delivery)."""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from school_dashboard import db, watchdog

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
EARLY = datetime(2026, 1, 1, tzinfo=UTC)  # first_seen long before every case below


def ny(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY)


def check(kind, last, now, first_seen=EARLY):
    return watchdog.check_digest_delivery(kind, last, now, NY, first_seen=first_seen)


# 2026-11-06 is a Friday.

def test_landed_digest_is_healthy():
    assert check("morning", ny(2026, 11, 6, 7, 1), ny(2026, 11, 6, 9, 0)) is None


def test_missing_digest_after_grace_is_a_problem():
    p = check("morning", ny(2026, 11, 5, 7, 1), ny(2026, 11, 6, 9, 0))
    assert p.source == "digest_morning"
    assert p.issue == "digest_missing"
    assert p.age_hours == 2.0


def test_within_grace_is_not_judged():
    assert check("morning", ny(2026, 11, 5, 7, 1), ny(2026, 11, 6, 8, 29)) is None


def test_no_row_at_all_is_a_problem_once_first_seen_has_passed():
    assert check("morning", None, ny(2026, 11, 6, 9, 0)).source == "digest_morning"


def test_first_deploy_cannot_fire():
    # Rule first seen at 8:00; the 7:00 digest predates it, so it is not judged.
    seen = ny(2026, 11, 6, 8, 0)
    assert check("morning", None, ny(2026, 11, 6, 9, 0), first_seen=seen) is None
    assert check("morning", None, ny(2026, 11, 6, 9, 0), first_seen=None) is None
    # Next day's 7:00 run came after first_seen, so a miss now counts.
    assert check("morning", None, ny(2026, 11, 7, 9, 0), first_seen=seen) is not None


def test_afternoon_skips_weekend():
    # Sat 18:00: latest afternoon slot is Fri 15:30, delivered.
    assert check("afternoon", ny(2026, 11, 6, 15, 31), ny(2026, 11, 7, 18, 0)) is None
    p = check("afternoon", ny(2026, 11, 5, 15, 31), ny(2026, 11, 7, 18, 0))
    assert p is not None and p.age_hours == 26.5


def test_weekly_friday_and_sunday():
    # Sun 2026-11-08 20:30: Sunday 19:00 slot has a row only in the healthy case.
    assert check("weekly_sunday", ny(2026, 11, 8, 19, 1), ny(2026, 11, 8, 20, 30)) is None
    assert check("weekly_sunday", ny(2026, 11, 1, 19, 1), ny(2026, 11, 8, 20, 30)) is not None
    # Friday slot judged on Saturday.
    assert check("weekly_friday", ny(2026, 11, 6, 15, 2), ny(2026, 11, 7, 10, 0)) is None


def test_dst_fall_back_is_judged_in_utc():
    # 2026-11-01 (Sunday) clocks fall back at 02:00. Night digest 20:30 EST = 01:30Z next day.
    now = ny(2026, 11, 1, 22, 30)  # EST
    row = ny(2026, 11, 1, 20, 31)
    assert check("night", row, now) is None
    assert check("night", ny(2026, 10, 31, 20, 31), now) is not None


def test_dst_spring_forward():
    # 2026-03-08 is spring forward (Sunday); 07:00 EDT = 11:00Z, a row at 06:59 EDT is too early.
    now = ny(2026, 3, 8, 9, 0)
    assert check("morning", ny(2026, 3, 8, 7, 1), now) is None
    assert check("morning", ny(2026, 3, 8, 6, 59), now) is not None


def test_alert_text_names_the_digest():
    p = check("morning", None, ny(2026, 11, 6, 9, 0))
    text = watchdog.format_alert([p])
    assert "Morning Briefing" in text and "not delivered" in text


# ---- schedule stays in step with docker/crontab ----

def _crontab_digests():
    out = {}
    for line in (Path(__file__).resolve().parent.parent / "docker" / "crontab").read_text().splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+\*\s+\*\s+(\S+)\s+root\s+bash\s+\S*run-(digest|weekly)\.sh\s+(\w+)", line)
        if not m:
            continue
        minute, hour, dow, _, mode = m.groups()
        if dow == "*":
            days = (0, 1, 2, 3, 4, 5, 6)
        elif "-" in dow:
            lo, hi = dow.split("-")
            days = tuple(range(int(lo) - 1, int(hi)))
        else:
            days = ((int(dow) - 1) % 7,)  # cron 0=Sun -> python 6
        key = mode if mode in ("morning", "afternoon", "night") else f"weekly_{mode}"
        out[key] = ((int(hour), int(minute)), days)
    return out


def test_digest_schedule_matches_crontab():
    expected = {k: (v.time, v.weekdays) for k, v in watchdog.DIGEST_SCHEDULE.items()}
    assert _crontab_digests() == expected


# ---- reading the digests table ----

def _make_db(path, rows):
    db.init_digests_table(str(path))
    conn = sqlite3.connect(path)
    with conn:
        for title, created in rows:
            conn.execute("INSERT INTO digests (id, created_at, title, cards) VALUES (?, ?, ?, '[]')",
                         (title + created, created, title))
    conn.close()


def test_latest_digests_reads_utc_and_groups_by_title(tmp_path):
    p = tmp_path / "school.db"
    _make_db(p, [("Morning Briefing", "2026-11-06 12:00:01"), ("Morning Briefing", "2026-11-05 12:00:01")])
    got = watchdog.latest_digests(p)
    assert got == {"Morning Briefing": datetime(2026, 11, 6, 12, 0, 1, tzinfo=UTC)}


def test_latest_digests_none_without_db_or_table(tmp_path):
    assert watchdog.latest_digests(tmp_path / "missing.db") is None
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert watchdog.latest_digests(empty) is None


# ---- run() integration ----

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TZ", "America/New_York")
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.delenv("WATCHDOG_ENABLED", raising=False)
    monkeypatch.delenv("WATCHDOG_DIGESTS_ENABLED", raising=False)
    for s in ("IXL", "SGY", "GC", "POWERSCHOOL"):
        monkeypatch.setenv(f"SOURCE_{s}_ENABLED", "0")
    return tmp_path


def _seed(tmp, first_seen):
    keys = [f"digest_{k}" for k in watchdog.DIGEST_SCHEDULE]
    (tmp / "watchdog.json").write_text(json.dumps({"first_seen": {k: first_seen for k in keys}}))


def test_run_no_db_no_digest_problem(env):
    r = watchdog.run(now=ny(2026, 11, 6, 9, 0), dry_run=True)
    assert not [p for p in r["problems"] if p["source"].startswith("digest_")]


def test_run_alerts_dedupes_and_recovers(env):
    _seed(env, "2026-01-01T00:00:00+00:00")
    _make_db(env / "school.db", [("Night Prep", "2026-11-05 01:31:00")])
    sent = []
    send = lambda topic, msg, title: sent.append((msg, title))  # noqa: E731
    now = ny(2026, 11, 6, 9, 0)
    r = watchdog.run(now=now, send=send)
    assert r["exit_code"] == 1
    assert any(p["source"] == "digest_morning" for p in r["problems"])
    assert len(sent) == 1 and "Morning Briefing" in sent[0][0]
    # Same problem 1h later: no new alert (single dedupe key).
    watchdog.run(now=now + timedelta(hours=1), send=send)
    assert len(sent) == 1
    # Morning digest lands (and others are healthy): recovery push.
    _make_db(env / "school.db", [("Morning Briefing", "2026-11-06 12:05:00"),
                                 ("Homework Check", "2026-11-05 20:31:00"),
                                 ("Night Prep", "2026-11-06 01:31:00"),
                                 ("Week in Review", "2026-11-06 20:01:00"),
                                 ("Week Ahead", "2026-11-08 00:01:00")])
    r = watchdog.run(now=now + timedelta(hours=2), send=send)
    assert any("Recovered" in m for m, _ in sent)


def test_run_digest_rule_can_be_disabled(env, monkeypatch):
    _seed(env, "2026-01-01T00:00:00+00:00")
    _make_db(env / "school.db", [("Night Prep", "2026-11-05 01:31:00")])
    monkeypatch.setenv("WATCHDOG_DIGESTS_ENABLED", "0")
    r = watchdog.run(now=ny(2026, 11, 6, 9, 0), dry_run=True)
    assert not [p for p in r["problems"] if p["source"].startswith("digest_")]


def test_run_records_digest_first_seen(env):
    _make_db(env / "school.db", [])
    watchdog.run(now=ny(2026, 11, 6, 9, 0), send=lambda *a: None)
    seen = json.loads((env / "watchdog.json").read_text())["first_seen"]
    assert "digest_morning" in seen
