"""Tests for /api/sync and /api/sync/status endpoints."""
import os
import pytest
from unittest.mock import patch, MagicMock
from web.app import app as flask_app


@pytest.fixture(autouse=True)
def reset_sync_state():
    """Ensure the sync lock and status are clean before each test."""
    import web.app as app_module
    if app_module._sync_lock.locked():
        app_module._sync_lock.release()
    app_module._sync_status.update({
        "running": False, "last_run": None, "last_result": None,
        "last_sources": [], "last_error": None,
    })
    yield


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNC_TOKEN", "test-secret")
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SCHOOL_DB_PATH", str(tmp_path / "school.db"))
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


@pytest.fixture
def client_no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("SYNC_TOKEN", raising=False)
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_sync_no_token_configured_returns_501(client_no_token):
    r = client_no_token.post("/api/sync",
                              json={"sources": "ixl,sgy"},
                              headers={"X-Sync-Token": "anything"})
    assert r.status_code == 501


def test_sync_missing_token_returns_401(client):
    r = client.post("/api/sync", json={"sources": "ixl,sgy"})
    assert r.status_code == 401


def test_sync_wrong_token_returns_401(client):
    r = client.post("/api/sync",
                    json={"sources": "ixl,sgy"},
                    headers={"X-Sync-Token": "wrong-token"})
    assert r.status_code == 401


@patch("web.app._run_sync_background")
def test_sync_valid_token_returns_202(mock_run, client):
    r = client.post("/api/sync",
                    json={"sources": "ixl,sgy", "digest": "none"},
                    headers={"X-Sync-Token": "test-secret"})
    assert r.status_code == 202
    data = r.get_json()
    assert data.get("started") is True


def test_sync_status_no_auth_required(client):
    r = client.get("/api/sync/status")
    assert r.status_code == 200
    data = r.get_json()
    assert "running" in data
    assert "last_run" in data
    assert "last_result" in data


def test_sync_status_idle_by_default(client):
    r = client.get("/api/sync/status")
    data = r.get_json()
    assert data["running"] is False


@patch("web.app._run_sync_background")
def test_sync_concurrent_returns_409(mock_run, client):
    import web.app as app_module
    # Force the lock to appear held
    acquired = app_module._sync_lock.acquire(blocking=False)
    assert acquired, "lock should have been free"
    try:
        r = client.post("/api/sync",
                        json={"sources": "ixl,sgy"},
                        headers={"X-Sync-Token": "test-secret"})
        assert r.status_code == 409
    finally:
        app_module._sync_lock.release()


@patch("web.app._run_sync_background")
def test_sync_default_sources_is_ixl_sgy(mock_run, client):
    r = client.post("/api/sync",
                    json={},
                    headers={"X-Sync-Token": "test-secret"})
    assert r.status_code == 202
    call_kwargs = mock_run.call_args
    sources = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("sources", "")
    assert "ixl" in sources
    assert "sgy" in sources


@patch("web.app.subprocess.run")
def test_run_sync_background_writes_meta_on_success(mock_sub, tmp_path, monkeypatch):
    """_run_sync_background writes sync_meta.json after each source completes."""
    import web.app as app_module
    from school_dashboard.sync_meta import read_sync_meta

    meta_path = str(tmp_path / "meta.json")
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", meta_path)
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SCHOOL_DB_PATH", str(tmp_path / "db.db"))
    monkeypatch.setenv("IXL_DIR", str(tmp_path / "ixl"))
    monkeypatch.setenv("SGY_FILE", str(tmp_path / "sgy.json"))
    monkeypatch.setenv("IXL_CRON", "")

    # IXL assigned returns valid JSON; subsequent calls (school-state update etc.) return empty stdout
    ixl_json = '{"totals": {"Math": {"assigned": 2, "done": 1, "remaining": 1}}, "remaining": []}'
    mock_sub.side_effect = [
        MagicMock(returncode=0, stdout=ixl_json, stderr=""),   # ixl assigned --json
        MagicMock(returncode=0, stdout="", stderr=""),          # school-state update
        MagicMock(returncode=0, stdout="", stderr=""),          # school-state html
    ]

    acquired = app_module._sync_lock.acquire(blocking=False)
    assert acquired, "lock should be free before test"

    app_module._run_sync_background("ixl", "none")

    meta = read_sync_meta(meta_path)
    assert meta.get("ixl", {}).get("last_result") == "ok"


@patch("web.app.subprocess.run")
def test_run_sync_background_writes_meta_on_error(mock_sub, tmp_path, monkeypatch):
    """_run_sync_background writes error result when scraper raises."""
    import web.app as app_module
    from school_dashboard.sync_meta import read_sync_meta

    meta_path = str(tmp_path / "meta.json")
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", meta_path)
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SCHOOL_DB_PATH", str(tmp_path / "db.db"))
    monkeypatch.setenv("IXL_DIR", str(tmp_path / "ixl"))
    monkeypatch.setenv("SGY_FILE", str(tmp_path / "sgy.json"))
    monkeypatch.setenv("IXL_CRON", "")

    mock_sub.side_effect = [Exception("scraper failed"), MagicMock(returncode=0), MagicMock(returncode=0)]

    acquired = app_module._sync_lock.acquire(blocking=False)
    assert acquired

    app_module._run_sync_background("ixl", "none")

    meta = read_sync_meta(meta_path)
    assert meta.get("ixl", {}).get("last_result") == "error"


def test_sync_meta_returns_empty_dict_when_no_file(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(tmp_path / "nonexistent.json"))
    r = client.get("/api/sync/meta")
    assert r.status_code == 200
    assert r.get_json() == {}


def test_sync_meta_returns_written_data(client, tmp_path, monkeypatch):
    from school_dashboard.sync_meta import write_sync_source
    p = str(tmp_path / "meta.json")
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", p)
    write_sync_source("ixl", "ok", path=p)
    r = client.get("/api/sync/meta")
    data = r.get_json()
    assert data["ixl"]["last_result"] == "ok"
    assert "last_run" in data["ixl"]


def test_format_freshness_shows_never_for_missing_sources():
    from web.app import _format_freshness
    result = _format_freshness({})
    assert "IXL: never pulled" in result
    assert "SGY: never pulled" in result
    assert "GC: never pulled" in result


def test_format_freshness_shows_days_ago():
    from web.app import _format_freshness
    from datetime import datetime, timedelta, timezone
    old_ts = (datetime.now(timezone.utc) - timedelta(days=12)).isoformat(timespec="seconds")
    meta = {"ixl": {"last_run": old_ts, "last_result": "ok"}}
    result = _format_freshness(meta)
    assert "12 days ago" in result
    assert "IXL" in result


def test_format_freshness_shows_today_for_recent():
    from web.app import _format_freshness
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {"sgy": {"last_run": ts, "last_result": "ok"}}
    result = _format_freshness(meta)
    assert "today" in result or "just now" in result


def test_format_freshness_shows_yesterday():
    from web.app import _format_freshness
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    meta = {"gc": {"last_run": ts, "last_result": "ok"}}
    result = _format_freshness(meta)
    assert "yesterday" in result


def test_assetlinks_returns_json_list(client):
    r = client.get("/.well-known/assetlinks.json")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, list)
    assert data[0]["relation"] == ["delegate_permission/common.handle_all_urls"]
    assert "namespace" in data[0]["target"]


def test_assetlinks_uses_env_package_name(client, monkeypatch):
    monkeypatch.setenv("TWA_PACKAGE_NAME", "com.example.myapp")
    r = client.get("/.well-known/assetlinks.json")
    data = r.get_json()
    assert data[0]["target"]["package_name"] == "com.example.myapp"
