"""Pure helpers of school_dashboard.hostcheck (S4b)."""
import logging

import pytest

from school_dashboard import hostcheck
from school_dashboard.digest import DASHBOARD_BASE


@pytest.fixture(autouse=True)
def _fresh():
    hostcheck.reset()
    yield
    hostcheck.reset()


@pytest.mark.parametrize("header,expected", [
    ("example.com", "example.com"),
    ("Example.COM", "example.com"),
    ("example.com:5000", "example.com"),
    ("example.com.", "example.com"),
    ("example.com.:443", "example.com"),
    ("127.0.0.1:5000", "127.0.0.1"),
    ("[::1]:5000", "[::1]"),
    ("[::1]", "[::1]"),
    ("[FE80::1]:80", "[fe80::1]"),
    ("my_host-1.lan", "my_host-1.lan"),
])
def test_normalise_ok(header, expected):
    assert hostcheck.normalise_host(header) == expected


@pytest.mark.parametrize("header", [
    None, "", " ", "a b", "a/b", "a@b", "a,b", "a\x00b", "a\nb", "a\tb", "exämple.com", "例え.jp",
    "a" * 254, ("a" * 63 + ".") * 5 + "com", "[::1", "[::1]x", "::1", "host:abc", "host:", ":80", ".", "[]",
    "evil.com, good.com", "a:1:2",
])
def test_normalise_malformed_is_none(header):
    assert hostcheck.normalise_host(header) is None


@pytest.mark.parametrize("raw,expected", [
    (None, set()), ("", set()), ("   ", set()),
    ("a.com", {"a.com"}),
    ("A.com, b.com:5000 c.com.", {"a.com", "b.com", "c.com"}),
    ("[::1]:5000,[FE80::1]", {"[fe80::1]"}),
    (".example.net", {".example.net"}),
    (".Example.NET:443", {".example.net"}),
    ("a.com,*,*.x.com,http://x.com/,u@x.com,exämple.com", {"a.com"}),
    ("x" * 254 + ".com", set()),
])
def test_parse_allowed(raw, expected):
    assert hostcheck.parse_allowed(raw) == frozenset(expected)


def test_parse_allowed_counts_invalid(caplog):
    with caplog.at_level(logging.WARNING):
        assert hostcheck.parse_allowed("a.com,*,b@c") == frozenset({"a.com"})
    assert any("2" in r.getMessage() and "ALLOWED_HOSTS" in r.getMessage() for r in caplog.records)


def test_parse_allowed_excludes_builtins_from_result_and_is_immutable():
    out = hostcheck.parse_allowed("a.com")
    assert isinstance(out, frozenset)
    assert not (out & hostcheck.builtin_hosts())


def test_builtins_include_dashboard_base_host():
    from urllib.parse import urlsplit
    b = hostcheck.builtin_hosts()
    assert {"localhost", "127.0.0.1", "[::1]"} <= b
    assert urlsplit(DASHBOARD_BASE).hostname in b


@pytest.mark.parametrize("host,allowed,ok", [
    ("localhost", set(), True), ("127.0.0.1", set(), True), ("[::1]", set(), True),
    ("x.test", {"x.test"}, True), ("y.test", {"x.test"}, False),
    ("a.example.net", {".example.net"}, True),
    ("a.b.example.net", {".example.net"}, True),
    ("example.net", {".example.net"}, False),
    ("badexample.net", {".example.net"}, False),
    ("example.net.evil.com", {".example.net"}, False),
    (None, {"x.test"}, False),
])
def test_is_allowed(host, allowed, ok):
    assert hostcheck.is_allowed(host, frozenset(allowed)) is ok


@pytest.mark.parametrize("env,expected", [
    (None, "log"), ("", "log"), ("off", "off"), ("LOG", "log"), (" enforce ", "enforce"), ("bogus", "log"),
])
def test_mode(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("HOST_CHECK_MODE", raising=False)
    else:
        monkeypatch.setenv("HOST_CHECK_MODE", env)
    assert hostcheck.mode() == expected


def test_unknown_mode_warns_once(monkeypatch, caplog):
    monkeypatch.setenv("HOST_CHECK_MODE", "bogus")
    with caplog.at_level(logging.WARNING):
        hostcheck.mode()
        hostcheck.mode()
    assert sum("HOST_CHECK_MODE" in r.getMessage() for r in caplog.records) == 1


A = frozenset({"ok.test"})


@pytest.mark.parametrize("host,allowed,mode,explicit,verdict", [
    ("localhost", A, "enforce", True, "allow"),
    ("ok.test", A, "enforce", True, "allow"),
    ("bad.test", A, "off", True, "allow"),
    ("bad.test", A, "log", True, "log"),
    ("bad.test", A, "enforce", True, "reject"),
    (None, A, "enforce", True, "reject"),
    (None, A, "log", True, "log"),
    ("bad.test", frozenset(), "enforce", False, "log"),
    ("bad.test", frozenset(), "log", False, "log"),
    ("bad.test", frozenset(), "off", False, "allow"),
])
def test_decide(host, allowed, mode, explicit, verdict):
    assert hostcheck.decide(host, allowed, mode, explicit) == verdict


def test_lockout_guard_warns_once(caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            hostcheck.decide("bad.test", frozenset(), "enforce", False)
    msgs = [r.getMessage() for r in caplog.records if "log-only" in r.getMessage()]
    assert len(msgs) == 1 and "ALLOWED_HOSTS is empty" in msgs[0]
