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

    mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")

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
