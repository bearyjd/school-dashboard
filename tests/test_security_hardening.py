"""WP3: trusted-proxy client IP, CSP report-only header + sink, generic sync last_error."""
import logging

import pytest

import web.app as app_module
from school_dashboard import csp, maintenance
from school_dashboard.rate_limit import trusted_client_ip
from web.app import app as flask_app

TOKEN = "TESTTOKEN123-abcdefghijklmnopqrstuvwxyz"


# -- trusted_client_ip (pure) -------------------------------------------------

@pytest.mark.parametrize("remote,xff,n,expected", [
    ("10.0.0.1", "203.0.113.5", 0, "10.0.0.1"),                      # default: header ignored
    ("10.0.0.1", "203.0.113.5", 1, "203.0.113.5"),
    ("10.0.0.1", "198.51.100.9, 203.0.113.5", 1, "203.0.113.5"),     # spoofed left entry ignored
    ("10.0.0.1", "198.51.100.9, 203.0.113.5", 2, "198.51.100.9"),
    ("10.0.0.1", "203.0.113.5", 2, "10.0.0.1"),                      # fewer hops than trusted -> remote
    ("10.0.0.1", "", 1, "10.0.0.1"),
    ("10.0.0.1", None, 1, "10.0.0.1"),
    ("10.0.0.1", "not-an-ip", 1, "10.0.0.1"),                        # garbage
    ("10.0.0.1", "203.0.113.5:8080", 1, "10.0.0.1"),
    ("10.0.0.1", "unknown", 1, "10.0.0.1"),
    ("10.0.0.1", "x" * 5000, 1, "10.0.0.1"),                         # oversize, nothing usable
    ("10.0.0.1", "x" * 2000 + ", 203.0.113.5", 1, "203.0.113.5"),    # oversize: right-hand entry intact
    ("10.0.0.1", "x" * 2000 + ", 203.0.113.5", 2, "10.0.0.1"),       # oversize: too short after truncation
    ("10.0.0.1", "1.2.3.4:5678", 1, "10.0.0.1"),                     # port suffix
    ("10.0.0.1", "[::1]:80", 1, "10.0.0.1"),
    ("10.0.0.1", "::ffff:192.0.2.7", 1, "::ffff:192.0.2.7"),
    ("10.0.0.1", "2001:db8::7", 1, "2001:db8::7"),
    ("10.0.0.1", " 203.0.113.5 ", 1, "203.0.113.5"),
])
def test_trusted_client_ip(remote, xff, n, expected):
    assert trusted_client_ip(remote, xff, n) == expected


def test_zone_id_and_mapped_ipv6_group_like_plain_addresses():
    from school_dashboard.rate_limit import client_key
    assert client_key(trusted_client_ip("10.0.0.1", "fe80::1%eth0", 1)) == "fe80::/64"
    assert client_key(trusted_client_ip("10.0.0.1", "::ffff:192.0.2.7", 1)) == "192.0.2.7"


def test_invalid_env_value_warns_once(caplog):
    from school_dashboard import rate_limit
    rate_limit._warned_proxy_values.clear()
    with caplog.at_level(logging.WARNING):
        assert rate_limit.trusted_proxy_count("abc") == 0
        assert rate_limit.trusted_proxy_count("abc") == 0
        assert rate_limit.trusted_proxy_count("") == 0
        assert rate_limit.trusted_proxy_count(None) == 0
        assert rate_limit.trusted_proxy_count("3") == 3
    warnings = [r for r in caplog.records if "TRUSTED_PROXY_COUNT" in r.getMessage()]
    assert len(warnings) == 1


# -- limiter integration ------------------------------------------------------

@pytest.fixture
def client(state_env, monkeypatch):
    monkeypatch.setenv("SYNC_TOKEN", TOKEN)
    app_module._sync_failures.clear()
    flask_app.config["TESTING"] = True
    yield flask_app.test_client()
    app_module._sync_failures.clear()


def _bad(client, xff, n=1, remote="10.0.0.1"):
    return [client.post("/api/sync", json={}, headers={"X-Sync-Token": "bad", "X-Forwarded-For": xff},
                        environ_overrides={"REMOTE_ADDR": remote}).status_code for _ in range(n)]


def test_default_ignores_forwarded_for(client, monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    _bad(client, "203.0.113.1", 10)
    assert _bad(client, "203.0.113.2") == [429]  # same remote_addr bucket


def test_trusted_proxy_separates_clients(client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    _bad(client, "203.0.113.1", 10)
    assert _bad(client, "203.0.113.2") == [401]
    assert _bad(client, "203.0.113.1") == [429]


def test_spoofed_left_entry_does_not_evade_limiter(client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    for i in range(10):
        _bad(client, f"198.51.100.{i}, 203.0.113.1")
    assert _bad(client, "198.51.100.99, 203.0.113.1") == [429]


@pytest.mark.parametrize("value", ["abc", "-1", "", "1.5", "999"])
def test_bad_env_value_falls_back_safely(client, monkeypatch, value):
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", value)
    _bad(client, "203.0.113.1", 10)
    assert _bad(client, "203.0.113.2") == [429]


def test_garbage_forwarded_for_uses_remote_addr(client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    _bad(client, "garbage", 10)
    assert _bad(client, "also garbage") == [429]


# -- CSP report-only ----------------------------------------------------------

CSP = app_module.CSP_REPORT_ONLY


@pytest.fixture(autouse=True)
def reset_csp_counters():
    csp.reset()
    yield
    csp.reset()


@pytest.fixture
def web(state_env, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><html><head></head><body></body></html>")
    monkeypatch.setattr(app_module, "_SPA_DIST", dist)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


@pytest.mark.parametrize("path", ["/app/", "/app/sync", "/legacy"])
def test_html_pages_carry_report_only_csp(web, path):
    r = web.get(path)
    assert r.headers["Content-Security-Policy-Report-Only"] == CSP
    assert "Content-Security-Policy" not in r.headers  # never enforced


@pytest.mark.parametrize("path", ["/api/sync/status", "/api/sync/meta", "/app/sw.js", "/app/assets/x.js"])
def test_non_html_has_no_csp(web, path):
    assert "Content-Security-Policy-Report-Only" not in web.get(path).headers


def test_csp_report_sink_204_and_logs_counts_only(web, caplog):
    secret = "https://example.invalid/Alpha-private-path"
    body = '{"csp-report":{"blocked-uri":"%s","document-uri":"%s"}}' % (secret, secret)
    with caplog.at_level(logging.INFO):
        r = web.post("/api/csp-report", data=body, content_type="application/csp-report")
    assert r.status_code == 204 and r.get_data() == b""
    assert "csp" in caplog.text.lower()
    assert "Alpha" not in caplog.text and "example.invalid" not in caplog.text and "blocked-uri" not in caplog.text


def test_csp_report_oversize_still_204_body_unlogged(web, caplog):
    with caplog.at_level(logging.INFO):
        r = web.post("/api/csp-report", data="A" * 100_000 + "SECRETMARK", content_type="application/csp-report")
    assert r.status_code == 204
    assert csp.counts() == {"received": 0, "oversize": 1}
    assert "SECRETMARK" not in caplog.text


def test_csp_report_without_content_length_counts_as_received(web):
    # No Content-Length -> treated as 0 bytes -> "received"; the body is never read either way.
    web.post("/api/csp-report")
    assert csp.counts() == {"received": 1, "oversize": 0}


def _csp_log_lines(caplog):
    return [r.getMessage() for r in caplog.records if "CSP reports so far" in r.getMessage()]


def test_csp_log_throttle_first_ten_then_every_hundredth(web, caplog):
    def post(n):
        for _ in range(n):
            web.post("/api/csp-report", data="{}")

    with caplog.at_level(logging.INFO):
        post(11)
        assert len(_csp_log_lines(caplog)) == 10
        post(88)  # total 99
        assert len(_csp_log_lines(caplog)) == 10
        post(1)  # the 100th
        assert len(_csp_log_lines(caplog)) == 11
        post(1)  # 101st
        assert len(_csp_log_lines(caplog)) == 11
    assert csp.counts()["received"] == 101


def test_csp_report_not_blocked_by_maintenance(web, monkeypatch):
    monkeypatch.setattr(maintenance, "status", lambda: {"reason": "test"})
    assert web.post("/api/csp-report", data="{}", content_type="application/csp-report").status_code == 204


def test_csp_report_get_not_allowed(web):
    assert web.get("/api/csp-report").status_code == 405


# -- generic last_error -------------------------------------------------------

def test_status_last_error_is_generic_and_detail_logged(web, caplog):
    detail = "school-state update failed: /srv/Beta/secret/path rc=2"
    try:
        with caplog.at_level(logging.ERROR):
            app_module._record_sync_error(detail)
        body = web.get("/api/sync/status").get_json()
        assert body["last_error"] and "Beta" not in body["last_error"] and "/srv" not in body["last_error"]
        assert detail in caplog.text
    finally:
        app_module._sync_status["last_error"] = None
