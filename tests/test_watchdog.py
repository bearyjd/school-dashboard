"""Tests for school_dashboard/watchdog.py."""
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from school_dashboard import cli, watchdog

NY = ZoneInfo("America/New_York")


def ny(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY)


def utc_iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def entry(ok_at, result="ok", run_at=None):
    e = {"last_run": utc_iso(run_at or ok_at), "last_result": result}
    if ok_at:
        e["last_ok"] = utc_iso(ok_at)
    return e


# 2026-11-06 is a Friday.
FRI_0630 = ny(2026, 11, 6, 6, 30)


# ---- expected_sync ----

def test_expected_sync_weekday_after_morning_run():
    assert watchdog.expected_sync(ny(2026, 11, 6, 9, 0), NY) == ny(2026, 11, 6, 6, 0)


def test_expected_sync_weekday_after_afternoon_run():
    assert watchdog.expected_sync(ny(2026, 11, 6, 16, 0), NY) == ny(2026, 11, 6, 14, 30)


def test_expected_sync_weekend_is_friday_afternoon():
    assert watchdog.expected_sync(ny(2026, 11, 7, 12, 0), NY) == ny(2026, 11, 6, 14, 30)
    assert watchdog.expected_sync(ny(2026, 11, 8, 23, 0), NY) == ny(2026, 11, 6, 14, 30)


def test_expected_sync_monday_before_six_is_friday():
    assert watchdog.expected_sync(ny(2026, 11, 9, 5, 0), NY) == ny(2026, 11, 6, 14, 30)


def test_expected_sync_dst_spring_forward_week():
    # DST began Sun 2026-03-08; Monday 06:00 is EDT (UTC-4).
    got = watchdog.expected_sync(ny(2026, 3, 9, 8, 0), NY)
    assert got.astimezone(timezone.utc) == datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)


def test_expected_sync_dst_fall_back_week():
    # DST ended Sun 2026-11-01; Monday 06:00 is EST (UTC-5).
    got = watchdog.expected_sync(ny(2026, 11, 2, 8, 0), NY)
    assert got.astimezone(timezone.utc) == datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc)


# ---- check_source ----

def test_healthy_after_morning_sync():
    now = ny(2026, 11, 6, 8, 30)
    assert watchdog.check_source("ixl", entry(ny(2026, 11, 6, 6, 5)), now, NY) is None


def test_no_alert_between_runs_before_grace_elapses():
    # Sync at 06:00 is still running at 06:10; yesterday's ok must not alert.
    now = ny(2026, 11, 6, 6, 10)
    assert watchdog.check_source("ixl", entry(ny(2026, 11, 5, 14, 35)), now, NY) is None


def test_overdue_when_morning_sync_missed():
    now = ny(2026, 11, 6, 8, 30)
    p = watchdog.check_source("ixl", entry(ny(2026, 11, 5, 14, 35)), now, NY)
    assert p is not None and p.issue == "stale"
    assert p.age_hours == pytest.approx(17.9, abs=0.1)


def test_no_alert_on_weekend_after_friday_afternoon_ok():
    now = ny(2026, 11, 8, 16, 30)
    assert watchdog.check_source("ixl", entry(ny(2026, 11, 6, 14, 35)), now, NY) is None


def test_monday_morning_after_weekend_healthy_if_friday_ok():
    # Before Monday's run + grace there is nothing to complain about.
    now = ny(2026, 11, 9, 7, 0)
    assert watchdog.check_source("ixl", entry(ny(2026, 11, 6, 14, 35)), now, NY) is None


def test_monday_morning_alert_when_monday_sync_missed():
    now = ny(2026, 11, 9, 8, 30)
    p = watchdog.check_source("ixl", entry(ny(2026, 11, 6, 14, 35)), now, NY)
    assert p is not None and p.issue == "stale"


def test_monday_morning_ok_run_clears():
    now = ny(2026, 11, 9, 8, 30)
    assert watchdog.check_source("ixl", entry(ny(2026, 11, 9, 6, 4)), now, NY) is None


LONG_AGO = ny(2026, 10, 1)


def test_never_run_source_alerts():
    p = watchdog.check_source("gc", None, ny(2026, 11, 6, 8, 30), NY, first_seen=LONG_AGO)
    assert p is not None and p.issue == "never_ok" and p.age_hours is None


def test_never_run_on_weekend_also_alerts_once_a_cycle_has_passed():
    p = watchdog.check_source("gc", None, ny(2026, 11, 7, 12, 0), NY, first_seen=LONG_AGO)
    assert p is not None and p.issue == "never_ok"


def test_never_ok_not_raised_without_first_seen():
    assert watchdog.check_source("gc", None, ny(2026, 11, 6, 8, 30), NY) is None


def test_never_ok_waits_for_one_scheduled_sync_after_first_seen():
    seen = ny(2026, 11, 6, 7, 0)  # Friday, after the 06:00 run
    # 14:30 run is the next chance; before it + grace there is no alert.
    assert watchdog.check_source("gc", None, ny(2026, 11, 6, 15, 0), NY, first_seen=seen) is None
    assert watchdog.check_source("gc", None, ny(2026, 11, 6, 15, 59), NY, first_seen=seen) is None
    p = watchdog.check_source("gc", None, ny(2026, 11, 6, 16, 1), NY, first_seen=seen)
    assert p is not None and p.issue == "never_ok"


def test_never_ok_first_seen_friday_evening_waits_for_monday():
    seen = ny(2026, 11, 6, 20, 0)
    assert watchdog.check_source("gc", None, ny(2026, 11, 9, 7, 0), NY, first_seen=seen) is None
    assert watchdog.check_source("gc", None, ny(2026, 11, 9, 7, 31), NY, first_seen=seen) is not None


def test_paused_entry_with_old_ok_is_judged_by_last_ok():
    e = entry(ny(2026, 11, 1, 6, 5), result="paused", run_at=ny(2026, 11, 1, 6, 5))
    p = watchdog.check_source("gc", e, ny(2026, 11, 6, 8, 30), NY)
    assert p is not None and p.issue == "stale"


def test_failing_when_error_and_ok_older_than_24h():
    now = ny(2026, 11, 6, 8, 30)
    e = entry(ny(2026, 11, 4, 14, 35), result="error", run_at=ny(2026, 11, 6, 6, 3))
    p = watchdog.check_source("gc", e, now, NY)
    assert p is not None and p.issue == "failing"


def test_single_error_with_recent_ok_is_not_failing_but_stale_check_applies():
    now = ny(2026, 11, 6, 8, 30)
    e = entry(ny(2026, 11, 5, 14, 35), result="error", run_at=ny(2026, 11, 6, 6, 3))
    p = watchdog.check_source("gc", e, now, NY)
    # ok is 17.9h old: not 'failing' (<24h) but the 06:00 run did not succeed -> stale
    assert p is not None and p.issue == "stale"


def test_legacy_entry_without_last_ok_uses_last_run():
    now = ny(2026, 11, 6, 8, 30)
    legacy = {"last_run": utc_iso(ny(2026, 11, 6, 6, 5)), "last_result": "ok"}
    assert watchdog.check_source("ixl", legacy, now, NY) is None


def test_legacy_error_entry_without_any_ok_is_never_ok():
    legacy = {"last_run": utc_iso(ny(2026, 11, 6, 6, 5)), "last_result": "error"}
    p = watchdog.check_source("ixl", legacy, ny(2026, 11, 6, 8, 30), NY, first_seen=LONG_AGO)
    assert p is not None and p.issue == "never_ok"


def test_garbage_timestamp_is_treated_as_unknown():
    p = watchdog.check_source("ixl", {"last_ok": "nope", "last_result": "ok"},
                              FRI_0630 + timedelta(hours=2), NY, first_seen=LONG_AGO)
    assert p is not None and p.issue == "never_ok"


# ---- evaluate (sources filtering, state file) ----

def test_evaluate_skips_sources_not_listed():
    probs = watchdog.evaluate({}, ["ixl"], FRI_0630 + timedelta(hours=2), NY, {"ixl": LONG_AGO, "gc": LONG_AGO})
    assert [p.source for p in probs] == ["ixl"]


def test_state_file_freshness(tmp_path):
    now = ny(2026, 11, 6, 8, 30)
    assert watchdog.check_mtime("state", ny(2026, 11, 6, 6, 5), now, NY) is None
    p = watchdog.check_mtime("state", ny(2026, 11, 4, 6, 5), now, NY)
    assert p is not None and p.issue == "stale"
    p = watchdog.check_mtime("state", None, now, NY, first_seen=LONG_AGO)
    assert p is not None and p.issue == "never_ok"
    assert watchdog.check_mtime("state", None, now, NY) is None


# ---- dedupe ----

def test_plan_first_alert_then_suppressed_then_realert():
    probs = [watchdog.Problem("ixl", "stale", 20.0)]
    now = datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc)
    alerts, recovered, new_state = watchdog.plan_alerts(probs, {}, now)
    assert [p.source for p in alerts] == ["ixl"] and recovered == []
    assert new_state["ixl"]["issue"] == "stale"
    assert new_state["ixl"]["first_alerted"] == new_state["ixl"]["last_alerted"]

    alerts, _, st2 = watchdog.plan_alerts(probs, new_state, now + timedelta(hours=8))
    assert alerts == [] and st2["ixl"]["last_alerted"] == new_state["ixl"]["last_alerted"]

    alerts, _, st3 = watchdog.plan_alerts(probs, new_state, now + timedelta(hours=25))
    assert len(alerts) == 1
    assert st3["ixl"]["first_alerted"] == new_state["ixl"]["first_alerted"]
    assert st3["ixl"]["last_alerted"] != new_state["ixl"]["last_alerted"]


def test_plan_issue_label_change_does_not_realert():
    now = datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc)
    _, _, st = watchdog.plan_alerts([watchdog.Problem("gc", "stale", 20.0)], {}, now)
    alerts, _, st2 = watchdog.plan_alerts([watchdog.Problem("gc", "failing", 30.0)], st, now + timedelta(hours=1))
    assert alerts == []
    assert st2["gc"]["issue"] == "failing"
    assert st2["gc"]["first_alerted"] == st["gc"]["first_alerted"]
    assert st2["gc"]["last_alerted"] == st["gc"]["last_alerted"]
    alerts, _, _ = watchdog.plan_alerts([watchdog.Problem("gc", "failing", 54.0)], st2, now + timedelta(hours=25))
    assert len(alerts) == 1


def test_plan_recovered_once():
    now = datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc)
    _, _, st = watchdog.plan_alerts([watchdog.Problem("gc", "stale", 20.0)], {}, now)
    alerts, recovered, st2 = watchdog.plan_alerts([], st, now + timedelta(hours=8))
    assert alerts == [] and recovered == ["gc"] and st2 == {}
    _, recovered, _ = watchdog.plan_alerts([], st2, now + timedelta(hours=16))
    assert recovered == []


def test_plan_tolerates_junk_state():
    now = datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc)
    alerts, _, st = watchdog.plan_alerts(
        [watchdog.Problem("ixl", "stale", 20.0)], {"ixl": "garbage", "x": {"issue": 3}}, now
    )
    assert len(alerts) == 1 and st["ixl"]["issue"] == "stale"


def test_load_state_corrupt_and_missing(tmp_path):
    p = tmp_path / "watchdog.json"
    assert watchdog.load_state(p) == {}
    p.write_text("{not json")
    assert watchdog.load_state(p) == {}
    p.write_text("[1,2]")
    assert watchdog.load_state(p) == {}


def test_save_state_atomic_roundtrip(tmp_path):
    p = tmp_path / "watchdog.json"
    watchdog.save_state(p, {"ixl": {"issue": "stale", "first_alerted": "a", "last_alerted": "b"}})
    assert watchdog.load_state(p)["ixl"]["issue"] == "stale"
    assert not list(tmp_path.glob(".watchdog*tmp"))


# ---- message ----

def test_message_friendly_names_one_decimal_and_footer():
    msg = watchdog.format_alert([
        watchdog.Problem("sgy", "stale", 19.0),
        watchdog.Problem("gc", "never_ok", None),
        watchdog.Problem("powerschool", "failing", 30.46),
        watchdog.Problem("state", "stale", 40.0),
        watchdog.Problem("ixl", "stale", 3.25),
    ])
    assert "Schoology" in msg and "GameChanger" in msg and "PowerSchool" in msg and "IXL" in msg
    assert "school data" in msg
    assert "19.0 hours" in msg and "30.5 hours" in msg
    assert "sgy" not in msg and "school-state.json" not in msg
    assert msg.splitlines()[-1] == "Dashboard may be showing old info."
    assert "token" not in msg.lower()


def test_recovered_message_friendly_names():
    assert watchdog.format_recovered(["gc", "state"]) == "Recovered: GameChanger, school data"


# ---- run() / CLI ----

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TZ", "America/New_York")
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.delenv("WATCHDOG_ENABLED", raising=False)
    for s in ("IXL", "SGY", "GC", "POWERSCHOOL"):
        monkeypatch.delenv(f"SOURCE_{s}_ENABLED", raising=False)
    monkeypatch.setenv("GC_TOKEN", "x")
    monkeypatch.setattr(watchdog, "_powerschool_configured", lambda: True)
    return tmp_path


def write_meta(tmp, data):
    (tmp / "sync_meta.json").write_text(json.dumps(data))
    if not (tmp / "watchdog.json").exists():  # sources seen long ago: never_ok may fire
        seen = utc_iso(LONG_AGO)
        (tmp / "watchdog.json").write_text(json.dumps(
            {"first_seen": {s: seen for s in ("ixl", "sgy", "gc", "powerschool", "state")}}))
    state = tmp / "school-state.json"
    state.write_text("{}")
    far = ny(2026, 11, 30).timestamp()  # never stale relative to the test clocks
    os.utime(state, (far, far))


def healthy_meta(at):
    return {s: entry(at) for s in ("ixl", "sgy", "gc", "powerschool")}


def test_run_healthy_exit_0_no_send(env):
    now = ny(2026, 11, 6, 8, 30)
    write_meta(env, healthy_meta(ny(2026, 11, 6, 6, 5)))
    import os
    os.utime(env / "school-state.json", (now.timestamp() - 3600,) * 2)
    sent = []
    res = watchdog.run(now=now, send=lambda t, m, ti: sent.append((t, m, ti)))
    assert res["exit_code"] == 0 and sent == []


def test_run_problem_sends_and_persists(env):
    now = ny(2026, 11, 6, 8, 30)
    meta = healthy_meta(ny(2026, 11, 6, 6, 5))
    meta["gc"] = entry(ny(2026, 11, 4, 6, 5), result="error", run_at=ny(2026, 11, 6, 6, 3))
    write_meta(env, meta)
    sent = []
    res = watchdog.run(now=now, send=lambda t, m, ti: sent.append((t, m, ti)))
    assert res["exit_code"] == 1
    assert sent and sent[0][2] == "School: data is stale" and "GameChanger" in sent[0][1]
    assert watchdog.load_state(env / "watchdog.json")["alerts"]["gc"]["issue"] == "failing"
    # second run same day: deduped, still exit 1
    sent.clear()
    res = watchdog.run(now=now + timedelta(hours=1), send=lambda *a: sent.append(a))
    assert res["exit_code"] == 1 and sent == []


def test_run_sends_recovery(env):
    now = ny(2026, 11, 6, 8, 30)
    meta = healthy_meta(ny(2026, 11, 6, 6, 5))
    meta["gc"] = entry(ny(2026, 11, 4, 6, 5), result="error", run_at=ny(2026, 11, 6, 6, 3))
    write_meta(env, meta)
    sent = []
    watchdog.run(now=now, send=lambda *a: sent.append(a))
    write_meta(env, healthy_meta(ny(2026, 11, 6, 14, 35)))
    sent.clear()
    res = watchdog.run(now=ny(2026, 11, 6, 16, 30), send=lambda *a: sent.append(a))
    assert res["exit_code"] == 0
    assert len(sent) == 1 and "recovered" in sent[0][1].lower() and "GameChanger" in sent[0][1]
    assert watchdog.load_state(env / "watchdog.json")["alerts"] == {}


def test_run_send_failure_keeps_alerts_unwritten(env):
    now = ny(2026, 11, 6, 8, 30)
    write_meta(env, {})

    def boom(*a):
        raise OSError("net")

    res = watchdog.run(now=now, send=boom)
    assert res["exit_code"] == 2
    assert "alerts" not in watchdog.load_state(env / "watchdog.json")


class FakeResp:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


def test_http_500_leaves_state_untouched_and_exit_2(env, monkeypatch):
    calls = []
    monkeypatch.setattr("school_dashboard.watchdog.requests.post",
                        lambda url, **kw: calls.append((url, kw)) or FakeResp(500))
    write_meta(env, {})
    res = watchdog.run(now=ny(2026, 11, 6, 8, 30))
    assert calls and calls[0][0] == "https://ntfy.sh/t" and calls[0][1]["timeout"] == 15
    assert res["exit_code"] == 2
    assert "alerts" not in watchdog.load_state(env / "watchdog.json")


def test_http_timeout_exit_2(env, monkeypatch):
    import requests

    def slow(url, **kw):
        raise requests.Timeout("t")

    monkeypatch.setattr("school_dashboard.watchdog.requests.post", slow)
    write_meta(env, {})
    assert watchdog.run(now=ny(2026, 11, 6, 8, 30))["exit_code"] == 2


def test_http_200_updates_state(env, monkeypatch):
    sent = []
    monkeypatch.setattr("school_dashboard.watchdog.requests.post",
                        lambda url, **kw: sent.append(kw) or FakeResp(200))
    write_meta(env, {})
    res = watchdog.run(now=ny(2026, 11, 6, 8, 30))
    assert res["exit_code"] == 1 and sent
    assert sent[0]["headers"]["Title"] == "School: data is stale"
    assert "ixl" in watchdog.load_state(env / "watchdog.json")["alerts"]


def test_first_seen_persisted_and_suppresses_never_ok(env):
    now = ny(2026, 11, 6, 8, 30)
    (env / "sync_meta.json").write_text("{}")
    (env / "school-state.json").write_text("{}")
    far = ny(2026, 11, 30).timestamp()
    os.utime(env / "school-state.json", (far, far))
    res = watchdog.run(now=now, send=lambda *a: pytest.fail("sent"))
    assert res["exit_code"] == 0
    seen = watchdog.load_state(env / "watchdog.json")["first_seen"]
    assert set(seen) == {"ixl", "sgy", "gc", "powerschool", "state"}
    # one scheduled sync (14:30) plus grace later, still nothing ok: now it alerts
    sent = []
    res = watchdog.run(now=ny(2026, 11, 6, 16, 5), send=lambda *a: sent.append(a))
    assert res["exit_code"] == 1 and sent


def test_disabled_source_first_seen_is_dropped(env, monkeypatch):
    now = ny(2026, 11, 6, 8, 30)
    write_meta(env, healthy_meta(ny(2026, 11, 6, 6, 5)))
    monkeypatch.setenv("SOURCE_GC_ENABLED", "0")
    watchdog.run(now=now, send=lambda *a: None)
    assert "gc" not in watchdog.load_state(env / "watchdog.json")["first_seen"]


def test_run_skips_disabled_and_paused_sources(env, monkeypatch):
    monkeypatch.setenv("SOURCE_GC_ENABLED", "0")
    monkeypatch.setenv("SOURCE_IXL_ENABLED", "0")
    now = ny(2026, 11, 6, 8, 30)
    meta = healthy_meta(ny(2026, 11, 6, 6, 5))
    del meta["gc"], meta["ixl"]
    write_meta(env, meta)
    import os
    os.utime(env / "school-state.json", (now.timestamp() - 3600,) * 2)
    res = watchdog.run(now=now, dry_run=True)
    assert res["exit_code"] == 0


def test_run_skips_unconfigured_gc_and_powerschool(env, monkeypatch):
    monkeypatch.delenv("GC_TOKEN", raising=False)
    monkeypatch.delenv("GC_EMAIL", raising=False)
    monkeypatch.setattr(watchdog, "_powerschool_configured", lambda: False)
    now = ny(2026, 11, 6, 8, 30)
    meta = healthy_meta(ny(2026, 11, 6, 6, 5))
    del meta["gc"], meta["powerschool"]
    write_meta(env, meta)
    import os
    os.utime(env / "school-state.json", (now.timestamp() - 3600,) * 2)
    assert watchdog.run(now=now, dry_run=True)["exit_code"] == 0


def test_run_kill_switch(env, monkeypatch):
    monkeypatch.setenv("WATCHDOG_ENABLED", "0")
    res = watchdog.run(now=ny(2026, 11, 6, 8, 30), send=lambda *a: pytest.fail("sent"))
    assert res["exit_code"] == 0 and res["skipped"] == "disabled"


def test_run_maintenance_skips(env):
    (env / "maintenance.json").write_text(json.dumps({"operation_id": "x", "since": "s", "reason": ""}))
    res = watchdog.run(now=ny(2026, 11, 6, 8, 30), send=lambda *a: pytest.fail("sent"))
    assert res["exit_code"] == 0 and res["skipped"] == "maintenance"


def test_run_no_topic_is_an_error_exit_2(env, monkeypatch, caplog):
    monkeypatch.delenv("NTFY_TOPIC")
    with caplog.at_level("ERROR"):
        res = watchdog.run(now=ny(2026, 11, 6, 8, 30), send=lambda *a: pytest.fail("sent"))
    assert res["exit_code"] == 2 and "NTFY_TOPIC" in res["error"]
    assert any(r.levelname == "ERROR" and "NTFY_TOPIC" in r.getMessage() for r in caplog.records)


def test_dry_run_neither_sends_nor_writes(env):
    write_meta(env, {})
    res = watchdog.run(now=ny(2026, 11, 6, 8, 30), dry_run=True, send=lambda *a: pytest.fail("sent"))
    assert res["exit_code"] == 1 and res["would_alert"]
    assert "alerts" not in watchdog.load_state(env / "watchdog.json")


def test_cli_dry_run_json(env, monkeypatch, capsys):
    write_meta(env, {})
    monkeypatch.setattr("sys.argv", ["school-state", "watchdog", "--dry-run", "--json"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["healthy"] is False
    assert {p["source"] for p in out["problems"]} >= {"ixl", "sgy"}
    assert "alerts" not in watchdog.load_state(env / "watchdog.json")


def test_cli_internal_error_exit_2(env, monkeypatch, capsys):
    def boom(**kw):
        raise RuntimeError("x")

    monkeypatch.setattr(watchdog, "run", boom)
    monkeypatch.setattr("sys.argv", ["school-state", "watchdog"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
