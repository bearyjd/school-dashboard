"""Watchdog registration and overview warning for recon_ingest (flag-guarded; flag off = byte-identical)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from school_dashboard import state, watchdog
from web.app import app as flask_app


@pytest.fixture(autouse=True)
def clean_env(state_env, tmp_path, monkeypatch):
    monkeypatch.delenv("RECON_INGEST_ENABLED", raising=False)
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setattr(state, "_config_cache", None)
    (state_env / "school-state.json").write_text("{}")
    return state_env


def _meta(state_dir, entry):
    (state_dir / "sync_meta.json").write_text(json.dumps({"recon_ingest": entry}))


def _overview():
    with flask_app.test_client() as c:
        return c.get("/api/overview").get_json()


def test_flag_off_monitored_sources_exclude_recon_ingest():
    assert "recon_ingest" not in watchdog.monitored_sources()


def test_flag_on_monitored_sources_include_recon_ingest(monkeypatch):
    base = watchdog.monitored_sources()
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    assert watchdog.monitored_sources() == [*base, "recon_ingest"]


def test_label_is_assignment_merge():
    assert watchdog._label("recon_ingest") == "Assignment merge"


def test_failing_ingest_is_a_watchdog_problem_when_on(monkeypatch):
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=3)).isoformat(timespec="seconds")
    meta = {"recon_ingest": {"last_run": now.isoformat(timespec="seconds"), "last_result": "error", "last_ok": old}}
    problems = watchdog.evaluate(meta, ["recon_ingest"], now, watchdog.get_tz(), {})
    assert [(p.source, p.issue) for p in problems] == [("recon_ingest", "failing")]


def test_overview_warnings_unchanged_with_flag_off(clean_env):
    _meta(clean_env, {"last_run": "2026-11-01T00:00:00+00:00", "last_result": "error"})
    assert not any("merge" in w.lower() for w in _overview()["warnings"])


def test_overview_has_one_plain_line_when_ingest_failed(clean_env, monkeypatch):
    base = _overview()["warnings"]
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _meta(clean_env, {"last_run": now, "last_result": "error"})
    extra = [w for w in _overview()["warnings"] if w not in base]
    assert len(extra) == 1 and "assignment merge" in extra[0].lower()


def test_overview_is_quiet_when_ingest_is_healthy(clean_env, monkeypatch):
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _meta(clean_env, {"last_run": now, "last_result": "ok", "last_ok": now})
    assert not any("merge" in w.lower() for w in _overview()["warnings"])


def test_overview_warns_when_ingest_is_stale(clean_env, monkeypatch):
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(timespec="seconds")
    _meta(clean_env, {"last_run": old, "last_result": "ok", "last_ok": old})
    assert any("assignment merge" in w.lower() for w in _overview()["warnings"])
