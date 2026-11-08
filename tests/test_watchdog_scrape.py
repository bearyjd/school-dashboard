"""Watchdog rule: a scraper that stays unavailable in scrape_meta for >= 24h raises one problem."""
import json
import os
from datetime import timedelta

import pytest
from test_watchdog import env, healthy_meta, ny, utc_iso, write_meta  # noqa: F401  (env is a fixture)

from school_dashboard import watchdog

NOW = ny(2026, 11, 6, 8, 30)


def state_with(source, since, child="Alpha"):
    return {"scrape_meta": {source: {"unavailable": {child: {"reason": "failed", "since": utc_iso(since)}}}}}


# ---- pure rule ----

def test_unavailable_for_a_day_is_a_problem():
    state = state_with("ixl", NOW - timedelta(hours=25))
    got = watchdog.check_scrape_degraded(state, ["ixl", "sgy"], NOW)
    assert got == [watchdog.Problem("ixl_scrape", "scrape_degraded", 25.0)]


def test_under_a_day_is_not_a_problem():
    state = state_with("schoology", NOW - timedelta(hours=23))
    assert watchdog.check_scrape_degraded(state, ["sgy"], NOW) == []


def test_oldest_entry_sets_the_age_and_one_problem_per_source():
    state = state_with("schoology", NOW - timedelta(hours=30), "Alpha")
    state["scrape_meta"]["schoology"]["unavailable"]["Beta"] = {
        "reason": "failed", "since": utc_iso(NOW - timedelta(hours=50))}
    got = watchdog.check_scrape_degraded(state, ["sgy"], NOW)
    assert got == [watchdog.Problem("sgy_scrape", "scrape_degraded", 50.0)]


def test_unwatched_source_and_garbage_are_ignored():
    state = state_with("ixl", NOW - timedelta(hours=40))
    assert watchdog.check_scrape_degraded(state, ["sgy"], NOW) == []
    assert watchdog.check_scrape_degraded({"scrape_meta": "x"}, ["ixl"], NOW) == []
    assert watchdog.check_scrape_degraded({}, ["ixl"], NOW) == []


def test_alert_text_names_the_source_without_child_names():
    text = watchdog.format_alert([watchdog.Problem("ixl_scrape", "scrape_degraded", 30.0)])
    assert "IXL" in text and "30.0 hours" in text and "Alpha" not in text


# ---- run() ----

def write_state(tmp, state):
    path = tmp / "school-state.json"
    path.write_text(json.dumps(state))
    far = ny(2026, 11, 30).timestamp()
    os.utime(path, (far, far))


def healthy_run_setup(env):
    write_meta(env, healthy_meta(ny(2026, 11, 6, 6, 5)))


def test_run_alerts_once_then_recovers(env):
    healthy_run_setup(env)
    write_state(env, state_with("ixl", NOW - timedelta(hours=26)))
    sent = []
    res = watchdog.run(now=NOW, send=lambda t, m, ti: sent.append((m, ti)))
    assert res["exit_code"] == 1
    assert [p["issue"] for p in res["problems"]] == ["scrape_degraded"]
    assert len(sent) == 1 and sent[0][1] == "School: data is stale"
    assert watchdog.load_state(env / "watchdog.json")["alerts"]["ixl_scrape"]["issue"] == "scrape_degraded"

    sent.clear()  # deduped within 24h
    assert watchdog.run(now=NOW + timedelta(hours=1), send=lambda *a: sent.append(a))["exit_code"] == 1
    assert sent == []

    write_state(env, {"scrape_meta": {"ixl": {"unavailable": {}}}})  # recovered
    res = watchdog.run(now=NOW + timedelta(hours=2), send=lambda t, m, ti: sent.append((m, ti)))
    assert res["exit_code"] == 0
    assert len(sent) == 1 and sent[0][0] == "Recovered: IXL scrape"
    assert watchdog.load_state(env / "watchdog.json")["alerts"] == {}


def test_run_realerts_after_24h(env):
    healthy_run_setup(env)
    write_state(env, state_with("schoology", NOW - timedelta(hours=26)))
    sent = []
    watchdog.run(now=NOW, send=lambda *a: sent.append(a))
    watchdog.run(now=NOW + timedelta(hours=25), send=lambda *a: sent.append(a))
    assert len(sent) == 2


@pytest.mark.parametrize("var, value", [("SOURCE_IXL_ENABLED", "0"), ("WATCHDOG_ENABLED", "0")])
def test_run_respects_switches(env, monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    meta = healthy_meta(ny(2026, 11, 6, 6, 5))
    meta.pop("ixl")
    write_meta(env, meta)
    write_state(env, state_with("ixl", NOW - timedelta(hours=40)))
    res = watchdog.run(now=NOW, send=lambda *a: pytest.fail("sent"))
    assert res["exit_code"] == 0


def test_run_skips_during_maintenance(env):
    healthy_run_setup(env)
    write_state(env, state_with("ixl", NOW - timedelta(hours=40)))
    (env / "maintenance.json").write_text(json.dumps({"operation_id": "x", "since": "s", "reason": ""}))
    res = watchdog.run(now=NOW, send=lambda *a: pytest.fail("sent"))
    assert res["skipped"] == "maintenance"
