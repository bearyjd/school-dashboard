"""SYNC_TOKEN is a server-side secret: never served in HTML/JS/JSON; /api/sync rate-limits bad tokens."""
import re

import pytest
from unittest.mock import patch

import web.app as app_module
from web.app import app as flask_app

TOKEN = "TESTTOKEN123"


@pytest.fixture(autouse=True)
def clean_state():
    if app_module._sync_lock.locked():
        app_module._sync_lock.release()
    app_module._sync_failures.clear()
    yield
    app_module._sync_failures.clear()


@pytest.fixture
def client(state_env, tmp_path, monkeypatch):
    monkeypatch.setenv("SYNC_TOKEN", TOKEN)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><html><head><title>x</title></head><body></body></html>")
    monkeypatch.setattr(app_module, "_SPA_DIST", dist)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def _everything(resp) -> str:
    headers = "\n".join(f"{k}: {v}" for k, v in resp.headers)
    return resp.get_data(as_text=True) + "\n" + headers


@pytest.mark.parametrize("path", ["/app/", "/app/sync", "/legacy", "/", "/api/sync/status", "/api/sync/meta"])
def test_token_absent_from_served_pages(client, path):
    resp = client.get(path)
    assert TOKEN not in _everything(resp)
    body = resp.get_data(as_text=True)
    assert "__SYNC_TOKEN__" not in body
    assert not re.search(r'name=["\']sync-token["\']', body)


def test_spa_index_has_no_script_injection(client):
    body = client.get("/app/").get_data(as_text=True)
    assert "<script>window" not in body


def test_legacy_sync_bar_degrades_gracefully(client):
    body = client.get("/legacy").get_data(as_text=True)
    assert "/app/sync" in body  # points users at the SPA Sync view
    assert "X-Sync-Token" not in body  # legacy page no longer sends the header


@patch("web.app._run_sync_background")
def test_sync_valid_token_still_202(_mock, client):
    r = client.post("/api/sync", json={"sources": "ixl"}, headers={"X-Sync-Token": TOKEN})
    assert r.status_code == 202
    assert TOKEN not in _everything(r)


def test_sync_wrong_and_missing_token_401_body_shape(client):
    for headers in ({}, {"X-Sync-Token": "nope"}):
        r = client.post("/api/sync", json={}, headers=headers)
        assert r.status_code == 401
        assert r.get_json() == {"error": "unauthorized"}
        assert TOKEN not in _everything(r)


def test_sync_501_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("SYNC_TOKEN")
    assert client.post("/api/sync", json={}, headers={"X-Sync-Token": "x"}).status_code == 501


def _bad(client, n):
    return [client.post("/api/sync", json={}, headers={"X-Sync-Token": "bad"}).status_code for _ in range(n)]


def test_limiter_429_after_ten_bad_attempts(client):
    codes = _bad(client, 12)
    assert codes[:10] == [401] * 10
    assert codes[10:] == [429, 429]
    r = client.post("/api/sync", json={}, headers={"X-Sync-Token": "bad"})
    assert int(r.headers["Retry-After"]) >= 1
    assert r.get_json()["error"] == "too many attempts"
    assert TOKEN not in _everything(r)


def test_limiter_blocks_correct_token_while_locked_out(client):
    _bad(client, 10)
    r = client.post("/api/sync", json={}, headers={"X-Sync-Token": TOKEN})
    assert r.status_code == 429


@patch("web.app._run_sync_background")
def test_limiter_recovers_after_window(_mock, client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(app_module._sync_failures, "_clock", lambda: now[0])
    _bad(client, 10)
    assert _bad(client, 1) == [429]
    now[0] += 61
    r = client.post("/api/sync", json={}, headers={"X-Sync-Token": TOKEN})
    assert r.status_code == 202


@patch("web.app._run_sync_background")
def test_success_resets_failure_count(_mock, client):
    _bad(client, 9)
    assert client.post("/api/sync", json={}, headers={"X-Sync-Token": TOKEN}).status_code == 202
    app_module._sync_lock.release()
    assert _bad(client, 9) == [401] * 9  # would have been 429 without the reset


def test_limiter_is_per_client_ip(client):
    _bad(client, 10)
    r = client.post("/api/sync", json={}, headers={"X-Sync-Token": "bad"},
                    environ_overrides={"REMOTE_ADDR": "10.9.9.9"})
    assert r.status_code == 401


def test_limiter_memory_bounded():
    from school_dashboard.rate_limit import FailureLimiter
    lim = FailureLimiter(max_failures=3, window=60, max_keys=50, clock=lambda: 1.0)
    for i in range(500):
        lim.record_failure(f"ip{i}")
    assert len(lim) <= 50
