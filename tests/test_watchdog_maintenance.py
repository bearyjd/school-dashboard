"""Watchdog rule: maintenance mode stuck ON for more than 2h (checked before the maintenance skip)."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from school_dashboard import watchdog

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 6, 8, 30, tzinfo=NY)


def utc_iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TZ", "America/New_York")
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.delenv("WATCHDOG_ENABLED", raising=False)
    return tmp_path


def _flag(env, since, op="op1"):
    (env / "maintenance.json").write_text(json.dumps({"operation_id": op, "since": since, "reason": "x"}))


def _stuck_flag(env, hours=3):
    _flag(env, utc_iso(NOW - timedelta(hours=hours)))


def test_check_maintenance_stuck_pure():
    f = watchdog.check_maintenance_stuck
    assert f(None, NOW) == []
    assert f({"since": utc_iso(NOW - timedelta(hours=1, minutes=59))}, NOW) == []
    (p,) = f({"since": utc_iso(NOW - timedelta(hours=3))}, NOW)
    assert (p.source, p.issue, p.age_hours) == ("maintenance_stuck", "maintenance_stuck", 3.0)
    # no/unparseable `since`: fall back to the flag file mtime, then to "unknown"
    old, recent = NOW - timedelta(hours=5), NOW - timedelta(minutes=10)
    for bad in ({"since": "garbage"}, {}, {"since": None, "corrupt": True}):
        assert [p.age_hours for p in f(bad, NOW, flag_mtime=old)] == [5.0]
        assert f(bad, NOW, flag_mtime=recent) == []
        (unknown,) = f(bad, NOW, flag_mtime=None)
        assert (unknown.source, unknown.age_hours) == ("maintenance_stuck", None)


def test_run_alerts_when_maintenance_stuck_before_skip(env):
    _stuck_flag(env)
    sent = []
    res = watchdog.run(now=NOW, send=lambda t, m, title: sent.append((t, m, title)))
    assert res["exit_code"] == 1 and res["skipped"] == "maintenance"
    assert [p["source"] for p in res["problems"]] == ["maintenance_stuck"]
    assert len(sent) == 1 and "maintenance" in sent[0][1].lower() and "3.0" in sent[0][1]
    assert "maintenance_stuck" in json.loads((env / "watchdog.json").read_text())["alerts"]


def test_maintenance_stuck_dedupes_and_realerts_after_24h(env):
    _stuck_flag(env)
    sent = []

    def send(*a):
        sent.append(a)

    watchdog.run(now=NOW, send=send)
    watchdog.run(now=NOW + timedelta(hours=2), send=send)
    assert len(sent) == 1
    watchdog.run(now=NOW + timedelta(hours=25), send=send)
    assert len(sent) == 2


def test_new_stuck_window_realerts_immediately(env):
    sent = []

    def send(*a):
        sent.append(a)

    _flag(env, utc_iso(NOW - timedelta(hours=3)), op="opA")
    watchdog.run(now=NOW, send=send)
    # off and on again with no normal run in between: a different window
    _flag(env, utc_iso(NOW - timedelta(hours=2, minutes=30)), op="opB")
    watchdog.run(now=NOW + timedelta(hours=1), send=send)
    assert len(sent) == 2
    watchdog.run(now=NOW + timedelta(hours=2), send=send)  # same window: deduped
    assert len(sent) == 2
    stored = json.loads((env / "watchdog.json").read_text())["alerts"]["maintenance_stuck"]
    assert stored["window"] == "opB"


def test_old_entry_without_window_keeps_24h_dedupe(env):
    _stuck_flag(env)
    (env / "watchdog.json").write_text(json.dumps({"alerts": {"maintenance_stuck": {
        "issue": "maintenance_stuck", "first_alerted": utc_iso(NOW), "last_alerted": utc_iso(NOW)}}}))
    sent = []
    watchdog.run(now=NOW + timedelta(hours=1), send=lambda *a: sent.append(a))
    assert sent == []


def test_maintenance_stuck_preserves_other_alert_keys_and_first_seen(env):
    state = {"alerts": {"ixl": {"issue": "stale", "first_alerted": "a", "last_alerted": utc_iso(NOW)}},
             "first_seen": {"ixl": utc_iso(NOW)}}
    (env / "watchdog.json").write_text(json.dumps(state))
    _stuck_flag(env)
    watchdog.run(now=NOW, send=lambda *a: None)
    stored = json.loads((env / "watchdog.json").read_text())
    assert "ixl" in stored["alerts"] and "maintenance_stuck" in stored["alerts"]
    assert stored["first_seen"] == state["first_seen"]


def test_maintenance_stuck_recovery_push_once_flag_cleared(env, monkeypatch):
    _stuck_flag(env)
    watchdog.run(now=NOW, send=lambda *a: None)
    (env / "maintenance.json").unlink()
    monkeypatch.setattr(watchdog, "monitored_sources", lambda: [])
    monkeypatch.setattr(watchdog, "_state_mtime", lambda: NOW - timedelta(hours=1))
    sent = []
    watchdog.run(now=NOW + timedelta(hours=1), send=lambda t, m, title: sent.append(m))
    assert any("maintenance" in m.lower() for m in sent)


def test_maintenance_active_under_2h_still_skips_silently(env):
    _flag(env, utc_iso(NOW - timedelta(minutes=30)))
    res = watchdog.run(now=NOW, send=lambda *a: pytest.fail("sent"))
    assert res["exit_code"] == 0 and res["skipped"] == "maintenance"


def test_maintenance_stuck_dry_run_sends_and_writes_nothing(env):
    _stuck_flag(env)
    res = watchdog.run(now=NOW, dry_run=True, send=lambda *a: pytest.fail("sent"))
    assert res["exit_code"] == 1 and len(res["would_alert"]) == 1
    assert not (env / "watchdog.json").exists()


def test_maintenance_stuck_delivery_failure_exit_2_and_retries(env):
    _stuck_flag(env)

    def boom(*a):
        raise RuntimeError("down")

    assert watchdog.run(now=NOW, send=boom)["exit_code"] == 2
    sent = []
    assert watchdog.run(now=NOW + timedelta(minutes=5), send=lambda *a: sent.append(a))["exit_code"] == 1
    assert len(sent) == 1


def test_watchdog_disabled_kill_switch_still_wins(env, monkeypatch):
    monkeypatch.setenv("WATCHDOG_ENABLED", "0")
    _stuck_flag(env)
    res = watchdog.run(now=NOW, send=lambda *a: pytest.fail("sent"))
    assert res["skipped"] == "disabled"


def test_run_alerts_for_flag_without_since_using_file_mtime(env):
    import os
    (env / "maintenance.json").write_text(json.dumps({"operation_id": "hand-made"}))
    old = (NOW - timedelta(hours=6)).timestamp()
    os.utime(env / "maintenance.json", (old, old))
    sent = []
    res = watchdog.run(now=NOW, send=lambda t, m, title: sent.append(m))
    assert res["exit_code"] == 1 and len(sent) == 1 and "6.0" in sent[0]


def test_run_alerts_once_for_corrupt_flag_with_fresh_mtime_unreadable_start(env, monkeypatch):
    (env / "maintenance.json").write_text("{not json")
    monkeypatch.setattr(watchdog, "_flag_mtime", lambda: None)
    sent = []
    watchdog.run(now=NOW, send=lambda t, m, title: sent.append(m))
    watchdog.run(now=NOW + timedelta(hours=1), send=lambda t, m, title: sent.append(m))
    assert len(sent) == 1 and "unknown" in sent[0].lower()
