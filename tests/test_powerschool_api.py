"""PowerSchool wiring in web/app.py: dashboard, has_data, freshness, /api/sync."""
import json
from unittest.mock import MagicMock, patch

import pytest

import web.app as app_module
from school_dashboard import powerschool, state
from school_dashboard.sync_meta import read_sync_meta
from web.app import app as flask_app

PS_STATE = {
    "powerschool": {
        "Alex": {
            "updated": "2026-11-05T06:00:00",
            "missing": [{"course": "Algebra", "title": "Quiz 4", "due": "2026-11-01", "category": None, "score": None}],
            "late": [],
            "upcoming": [],
        }
    },
    "powerschool_meta": {"warnings": ["PowerSchool scrape not applied: stale"]},
}


@pytest.fixture(autouse=True)
def reset(tmp_path, monkeypatch):
    state._config_cache = None
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "none.json"))
    if app_module._sync_lock.locked():
        app_module._sync_lock.release()
    yield
    state._config_cache = None


def test_dashboard_exposes_powerschool_per_child(tmp_path, monkeypatch):
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps(PS_STATE))
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(sp))
    with flask_app.test_client() as c:
        body = c.get("/api/dashboard").get_json()
    assert body["powerschool"]["Alex"]["missing"][0]["title"] == "Quiz 4"
    assert body["powerschool"]["Alex"]["late"] == []
    assert body["has_data"]["Alex"]["ps"] is True
    assert body["powerschool_warnings"] == ["PowerSchool scrape not applied: stale"]


def test_format_freshness_includes_powerschool():
    assert "POWERSCHOOL: never pulled" in app_module._format_freshness({})


def test_chat_prompt_includes_powerschool_block(tmp_path, monkeypatch):
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps(PS_STATE))
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(sp))
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(tmp_path / "m.json"))
    assert "MISSING (1)" in app_module.build_system_prompt()


def _sync_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(tmp_path / "meta.json"))
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SCHOOL_DB_PATH", str(tmp_path / "db.db"))
    monkeypatch.setenv("PS_FILE", str(tmp_path / "ps.json"))
    monkeypatch.delenv("SOURCE_POWERSCHOOL_ENABLED", raising=False)


@patch("web.app.subprocess.run")
def test_sync_powerschool_runs_scraper_and_state_update(mock_sub, tmp_path, monkeypatch):
    _sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(powerschool, "skip_reason", lambda: "")
    mock_sub.return_value = MagicMock(returncode=0, stdout='{"children": []}', stderr="")
    assert app_module._sync_lock.acquire(blocking=False)
    app_module._run_sync_background("powerschool", "none")
    assert read_sync_meta(str(tmp_path / "meta.json"))["powerschool"]["last_result"] == "ok"
    assert (tmp_path / "ps.json").read_text() == '{"children": []}'
    update = [c.args[0] for c in mock_sub.call_args_list if c.args[0][:2] == ["school-state", "update"]]
    assert update and "--ps-file" in update[0]


@patch("web.app.subprocess.run")
def test_sync_powerschool_skips_cleanly_when_not_installed(mock_sub, tmp_path, monkeypatch):
    _sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(powerschool, "skip_reason", lambda: "powerschool command not found")
    assert app_module._sync_lock.acquire(blocking=False)
    app_module._run_sync_background("powerschool", "none")
    assert app_module._sync_status["last_error"] is None
    assert "powerschool" not in read_sync_meta(str(tmp_path / "meta.json"))
    assert not any(c.args[0][0] == "powerschool" for c in mock_sub.call_args_list)


@patch("web.app.subprocess.run")
def test_sync_powerschool_failure_is_error_and_keeps_old_file(mock_sub, tmp_path, monkeypatch):
    _sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(powerschool, "skip_reason", lambda: "")
    (tmp_path / "ps.json").write_text("OLD")
    mock_sub.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    assert app_module._sync_lock.acquire(blocking=False)
    app_module._run_sync_background("powerschool", "none")
    assert read_sync_meta(str(tmp_path / "meta.json"))["powerschool"]["last_result"] == "error"
    assert (tmp_path / "ps.json").read_text() == "OLD"


@patch("web.app.subprocess.run")
def test_sync_powerschool_paused(mock_sub, tmp_path, monkeypatch):
    _sync_env(tmp_path, monkeypatch)
    monkeypatch.setenv("SOURCE_POWERSCHOOL_ENABLED", "off")
    assert app_module._sync_lock.acquire(blocking=False)
    app_module._run_sync_background("powerschool", "none")
    assert read_sync_meta(str(tmp_path / "meta.json"))["powerschool"]["last_result"] == "paused"
    mock_sub.assert_not_called()


def test_sync_meta_failure_when_output_has_warnings(tmp_path, monkeypatch):
    _sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(powerschool, "skip_reason", lambda: "")
    (tmp_path / "ps.json").write_text("OLD")
    with patch("web.app.subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout='{"children": [], "warnings": ["login"]}', stderr="")
        assert app_module._sync_lock.acquire(blocking=False)
        app_module._run_sync_background("powerschool", "none")
    assert read_sync_meta(str(tmp_path / "meta.json"))["powerschool"]["last_result"] == "error"
    assert (tmp_path / "ps.json").read_text() == "OLD"


def test_sync_meta_failure_when_output_not_json(tmp_path, monkeypatch):
    _sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(powerschool, "skip_reason", lambda: "")
    with patch("web.app.subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="<html>", stderr="")
        assert app_module._sync_lock.acquire(blocking=False)
        app_module._run_sync_background("powerschool", "none")
    assert read_sync_meta(str(tmp_path / "meta.json"))["powerschool"]["last_result"] == "error"
    assert not (tmp_path / "ps.json").exists()


def test_scrape_file_is_private_and_leaves_no_temp(tmp_path):
    with patch("web.app.subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout='{"children": []}', stderr="")
        app_module._scrape_powerschool(str(tmp_path / "ps.json"))
    assert (tmp_path / "ps.json").stat().st_mode & 0o777 == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["ps.json"]


def test_chat_prompt_does_not_duplicate_powerschool_state(tmp_path, monkeypatch):
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps(PS_STATE))
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(sp))
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(tmp_path / "m.json"))
    prompt = app_module.build_system_prompt()
    assert '"powerschool"' not in prompt and "untrusted" in prompt.lower()


def test_sync_rejects_unknown_source(monkeypatch):
    monkeypatch.setenv("SYNC_TOKEN", "tok-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    with flask_app.test_client() as c:
        r = c.post("/api/sync", json={"sources": "ixl,bogus"}, headers={"X-Sync-Token": "tok-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"})
    assert r.status_code == 400 and "bogus" in r.get_json()["error"]
    assert not app_module._sync_lock.locked()


@patch("web.app._run_sync_background")
def test_sync_accepts_allowlisted_sources(mock_run, monkeypatch):
    monkeypatch.setenv("SYNC_TOKEN", "tok-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    with flask_app.test_client() as c:
        for src in ("all", "powerschool", "ixl, sgy ,gc"):
            if app_module._sync_lock.locked():
                app_module._sync_lock.release()
            r = c.post("/api/sync", json={"sources": src}, headers={"X-Sync-Token": "tok-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"})
            assert r.status_code == 202, src


def test_sync_token_uses_constant_time_compare(monkeypatch):
    monkeypatch.setenv("SYNC_TOKEN", "tok-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    with patch("web.app.hmac.compare_digest", return_value=False) as cmp:
        with flask_app.test_client() as c:
            r = c.post("/api/sync", json={"sources": "ixl"}, headers={"X-Sync-Token": "tok-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"})
    assert r.status_code == 401 and cmp.called


@patch("web.app.subprocess.run")
def test_sync_all_includes_powerschool(mock_sub, tmp_path, monkeypatch):
    _sync_env(tmp_path, monkeypatch)
    monkeypatch.setenv("SOURCE_POWERSCHOOL_ENABLED", "off")
    monkeypatch.setenv("SOURCE_IXL_ENABLED", "off")
    monkeypatch.setenv("SOURCE_SGY_ENABLED", "off")
    monkeypatch.setenv("SOURCE_GC_ENABLED", "off")
    assert app_module._sync_lock.acquire(blocking=False)
    app_module._run_sync_background("all", "none")
    assert read_sync_meta(str(tmp_path / "meta.json"))["powerschool"]["last_result"] == "paused"
