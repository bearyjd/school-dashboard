"""Host-header allowlist wired into the Flask app (S4b)."""
import logging

import pytest

from school_dashboard import hostcheck

V1 = [("get", "/"), ("get", "/api/items"), ("get", "/api/overview"), ("post", "/api/sync"),
      ("get", "/app/"), ("post", "/api/csp-report")]
V2 = [("get", "/api/v2/config"), ("post", "/api/v2/assignments/1/stage")]


@pytest.fixture
def client(state_env, monkeypatch):
    for var in ("ALLOWED_HOSTS", "HOST_CHECK_MODE", "RECON_WRITE_TOKEN", "RECON_WRITE_OPEN"):
        monkeypatch.delenv(var, raising=False)
    hostcheck.reset()
    from web.app import app
    return app.test_client()


def hdr(host):
    return {"Host": host}


def warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and r.name == "school_dashboard.hostcheck"]


def enforce(monkeypatch, allowed="ok.test"):
    monkeypatch.setenv("HOST_CHECK_MODE", "enforce")
    monkeypatch.setenv("ALLOWED_HOSTS", allowed)


def test_default_is_log_only_and_warns_once(client, caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert client.get("/api/sync/status", headers=hdr("stranger.test")).status_code == 200
    w = [m for m in warnings(caplog) if "stranger.test" in m]
    assert len(w) == 1


def test_off_is_silent(client, monkeypatch, caplog):
    monkeypatch.setenv("HOST_CHECK_MODE", "off")
    with caplog.at_level(logging.WARNING):
        assert client.get("/api/sync/status", headers=hdr("stranger.test")).status_code == 200
    assert warnings(caplog) == []


def test_default_client_host_is_builtin(client, caplog):
    with caplog.at_level(logging.WARNING):
        assert client.get("/api/sync/status").status_code == 200
    assert warnings(caplog) == []


@pytest.mark.parametrize("method,path", V1 + V2)
def test_enforce_rejects_unknown_everywhere(client, monkeypatch, method, path):
    enforce(monkeypatch)
    r = getattr(client, method)(path, headers=hdr("evil.test:5000"))
    assert r.status_code == 421
    assert r.get_json() == {"error": "host_not_allowed"}
    assert r.headers["Cache-Control"] == "no-store"
    assert b"evil" not in r.data
    assert all("evil" not in v for v in r.headers.values())


@pytest.mark.parametrize("host", ["ok.test", "OK.test:8080", "localhost", "localhost:5000", "127.0.0.1:5000",
                                  "[::1]:5000", "school.example.invalid", "a.sub.test"])
def test_enforce_allows(client, monkeypatch, host):
    enforce(monkeypatch, "ok.test, .sub.test, school.example.invalid")
    assert client.get("/api/sync/status", headers=hdr(host)).status_code == 200


def test_enforce_suffix_does_not_match_apex_or_lookalike(client, monkeypatch):
    enforce(monkeypatch, ".sub.test")
    for host in ("sub.test", "badsub.test"):
        assert client.get("/api/sync/status", headers=hdr(host)).status_code == 421


@pytest.mark.parametrize("bad", ["a b", "a@b", "a/b", "exämple.com", "x" * 300])
def test_enforce_rejects_malformed(client, monkeypatch, bad):
    enforce(monkeypatch)
    r = client.get("/api/sync/status", headers=hdr(bad.encode("latin-1", "ignore").decode("latin-1")))
    assert r.status_code == 421


@pytest.mark.parametrize("allowed", [None, "", "  ", "*", "*,x@c,y/z"])
def test_enforce_without_valid_list_does_not_lock_out(client, monkeypatch, caplog, allowed):
    monkeypatch.setenv("HOST_CHECK_MODE", "enforce")
    if allowed is not None:
        monkeypatch.setenv("ALLOWED_HOSTS", allowed)
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            assert client.get("/api/sync/status", headers=hdr("stranger.test")).status_code == 200
    assert sum("ALLOWED_HOSTS is empty" in m for m in warnings(caplog)) == 1


def test_unknown_mode_falls_back_to_log(client, monkeypatch):
    monkeypatch.setenv("HOST_CHECK_MODE", "strict")
    monkeypatch.setenv("ALLOWED_HOSTS", "ok.test")
    assert client.get("/api/sync/status", headers=hdr("stranger.test")).status_code == 200


def test_x_forwarded_host_cannot_help_or_hurt(client, monkeypatch):
    enforce(monkeypatch)
    r = client.get("/api/sync/status", headers={"Host": "evil.test", "X-Forwarded-Host": "ok.test"})
    assert r.status_code == 421
    r = client.get("/api/sync/status", headers={"Host": "ok.test", "X-Forwarded-Host": "evil.test"})
    assert r.status_code == 200


def test_runs_before_maintenance_gate(client, monkeypatch):
    enforce(monkeypatch)
    from school_dashboard import maintenance
    monkeypatch.setattr(maintenance, "status", lambda: {"reason": "t"})
    assert client.post("/api/items", json={}, headers=hdr("evil.test")).status_code == 421
    assert client.post("/api/items", json={}, headers=hdr("ok.test")).status_code == 503


def test_runs_before_recon_write_token_check(client, monkeypatch):
    enforce(monkeypatch)
    path = "/api/v2/assignments/1/stage"
    assert client.post(path, json={}, headers=hdr("evil.test")).status_code == 421
    assert client.post(path, json={}, headers=hdr("ok.test")).status_code != 421


def test_log_set_is_bounded_and_values_truncated(client, caplog):
    with caplog.at_level(logging.WARNING):
        for i in range(70):
            client.get("/api/sync/status", headers=hdr(f"h{i}.test"))
        client.get("/api/sync/status", headers=hdr("z" * 200 + ".test"))
    w = warnings(caplog)
    assert sum(" h" in m or "h0.test" in m for m in w) <= 64 + 1
    assert not any("z" * 81 in m for m in w)


def test_malformed_logged_as_literal(client, caplog):
    with caplog.at_level(logging.WARNING):
        client.get("/api/sync/status", headers=hdr("a@b"))
    assert any("<malformed>" in m for m in warnings(caplog))
    assert not any("a@b" in m for m in warnings(caplog))
