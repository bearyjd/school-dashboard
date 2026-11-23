"""Host allowlist review round: built-in-only enforce, visibility, robustness."""
import logging

import pytest
from flask import Flask

from school_dashboard import hostcheck


@pytest.fixture
def client(state_env, monkeypatch):
    for var in ("ALLOWED_HOSTS", "HOST_CHECK_MODE", "RECON_WRITE_TOKEN", "RECON_WRITE_OPEN"):
        monkeypatch.delenv(var, raising=False)
    hostcheck.reset()
    from web.app import app
    return app.test_client()


def recs(caplog, level):
    return [r.getMessage() for r in caplog.records
            if r.levelno == level and r.name == "school_dashboard.hostcheck"]


def get(client, host, **extra):
    return client.get("/api/sync/status", headers={"Host": host, **extra})


# ---- 1. built-in-only allowlists enforce ----
@pytest.mark.parametrize("allowed", ["PUBLIC", "localhost", "127.0.0.1, [::1]"])
def test_builtin_only_list_enforces(client, monkeypatch, caplog, allowed, dashboard_url):
    from urllib.parse import urlsplit
    monkeypatch.setenv("HOST_CHECK_MODE", "enforce")
    monkeypatch.setenv("ALLOWED_HOSTS", allowed.replace("PUBLIC", urlsplit(dashboard_url).hostname))
    with caplog.at_level(logging.WARNING):
        assert get(client, "stranger.test").status_code == 421
        assert get(client, "localhost").status_code == 200
    assert not any("empty" in m for m in recs(caplog, logging.WARNING))


@pytest.mark.parametrize("allowed", [None, "", "*,x@c"])
def test_empty_or_invalid_never_rejects_and_says_so(client, monkeypatch, caplog, allowed):
    monkeypatch.setenv("HOST_CHECK_MODE", "enforce")
    if allowed is not None:
        monkeypatch.setenv("ALLOWED_HOSTS", allowed)
    with caplog.at_level(logging.WARNING):
        assert get(client, "stranger.test").status_code == 200
    assert any("no valid entries" in m for m in recs(caplog, logging.WARNING))


# ---- 2. visibility ----
def test_effective_config_logged_once_on_first_request(client, monkeypatch, caplog):
    monkeypatch.setenv("HOST_CHECK_MODE", "enforce")
    monkeypatch.setenv("ALLOWED_HOSTS", "ok.test,*,a@b")
    with caplog.at_level(logging.INFO, logger="school_dashboard.hostcheck"):
        get(client, "evil-first.test")
        get(client, "evil-second.test")
    info = recs(caplog, logging.INFO)
    assert len(info) == 1
    assert "enforce" in info[0] and "1" in info[0] and "2" in info[0]
    assert not any("evil" in m for m in info)


def test_effective_mode_is_log_when_enforce_cannot_apply(client, monkeypatch, caplog):
    monkeypatch.setenv("HOST_CHECK_MODE", "enforce")
    with caplog.at_level(logging.INFO, logger="school_dashboard.hostcheck"):
        get(client, "localhost")
    info = recs(caplog, logging.INFO)
    assert len(info) == 1 and "effective=log" in info[0] and "requested=enforce" in info[0]


def test_bound_reached_warns_once(client, caplog):
    with caplog.at_level(logging.WARNING):
        for i in range(75):
            get(client, f"h{i}.test")
    w = [m for m in recs(caplog, logging.WARNING) if "not logged individually" in m]
    assert w == ["further unknown hosts are not logged individually"]


@pytest.mark.parametrize("mode", ["log", "enforce"])
def test_proxy_with_loopback_host_warns_once(client, monkeypatch, caplog, mode):
    monkeypatch.setenv("HOST_CHECK_MODE", mode)
    monkeypatch.setenv("ALLOWED_HOSTS", "ok.test")
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert get(client, "127.0.0.1:5000", **{"X-Forwarded-For": "evil-ip-9.9.9.9"}).status_code == 200
    w = [m for m in recs(caplog, logging.WARNING) if "loopback Host" in m]
    assert len(w) == 1 and "cannot protect proxied traffic" in w[0]
    assert not any("evil" in m or "9.9.9.9" in m for m in recs(caplog, logging.WARNING))


def test_proxy_warning_not_for_plain_loopback_nor_off(client, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        get(client, "127.0.0.1:5000")
        get(client, "ok.test", **{"X-Forwarded-For": "1.2.3.4"})
        monkeypatch.setenv("HOST_CHECK_MODE", "off")
        get(client, "127.0.0.1", **{"X-Forwarded-For": "1.2.3.4"})
    assert not any("loopback Host" in m for m in recs(caplog, logging.WARNING))


def test_mode_typo_warns_on_first_request_even_for_known_host(client, monkeypatch, caplog):
    monkeypatch.setenv("HOST_CHECK_MODE", "Enforce!")
    with caplog.at_level(logging.WARNING):
        get(client, "localhost")
    w = recs(caplog, logging.WARNING)
    assert sum("HOST_CHECK_MODE" in m for m in w) == 1
    assert not any("Enforce!" in m for m in w)


# ---- 3. robustness ----
def _app_with_other_hook():
    app = Flask(__name__)
    calls: list[str] = []
    app.before_request(lambda: calls.append("other"))
    app.add_url_rule("/x", "x", lambda: "ok")
    return app, calls


def test_init_app_runs_first_even_after_other_hooks(monkeypatch):
    monkeypatch.setenv("HOST_CHECK_MODE", "enforce")
    monkeypatch.setenv("ALLOWED_HOSTS", "ok.test")
    app, calls = _app_with_other_hook()
    hostcheck.init_app(app)
    assert app.test_client().get("/x", headers={"Host": "evil.test"}).status_code == 421
    assert calls == []
    assert app.test_client().get("/x", headers={"Host": "ok.test"}).status_code == 200
    assert calls == ["other"]


def test_init_app_is_idempotent():
    app, _ = _app_with_other_hook()
    hostcheck.init_app(app)
    hostcheck.init_app(app)
    assert app.before_request_funcs[None].count(hostcheck._check) == 1


@pytest.mark.parametrize("header", ["a..b", ".example.net", "a..", "..a", "example.com..", "host:0", "host:65536",
                                    "host:99999", "host:123456", "host:-1", "[::1]:0", "[::1]:70000", "a.b..c:80"])
def test_normalise_rejects_empty_labels_and_bad_ports(header):
    assert hostcheck.normalise_host(header) is None


@pytest.mark.parametrize("header,expected", [("host:1", "host"), ("host:65535", "host"), ("a.b.:80", "a.b"),
                                             ("host:00080", "host")])
def test_normalise_accepts_port_range(header, expected):
    assert hostcheck.normalise_host(header) == expected


@pytest.mark.parametrize("entry", ["..example.net", "a..b", ".", "a.b..", "host:0"])
def test_entries_with_empty_labels_are_invalid(entry):
    assert hostcheck.parse_allowed(entry) == frozenset()
