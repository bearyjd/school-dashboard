"""Host-header allowlist for the whole Flask app (log-only by default).

Only the raw `Host` header is judged: never X-Forwarded-Host, never SERVER_NAME. Env is read per request.
Env: ALLOWED_HOSTS (comma/space separated; `.example.net` is a suffix rule), HOST_CHECK_MODE (off|log|enforce).
`enforce` without at least one valid ALLOWED_HOSTS entry (built-in names count) behaves as `log` (lock-out guard).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from urllib.parse import urlsplit

from flask import Flask, Response, request

from school_dashboard.digest import DASHBOARD_BASE

_log = logging.getLogger(__name__)
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
MODES = ("off", "log", "enforce")
MAX_HOST_LEN = 253
MAX_LOGGED_HOSTS = 64
MAX_LOGGED_LEN = 80
_NAME = re.compile(r"[a-z0-9._-]+")
_V6 = re.compile(r"\[[0-9a-f:.]+\]")
_PORT = re.compile(r"\d{1,5}")
MAX_PORT = 65535
_SPLIT = re.compile(r"[,\s]+")

_lock = threading.Lock()
_seen: set[str] = set()
_warned: set[str] = set()


def reset() -> None:
    """Forget what was already logged (tests)."""
    with _lock:
        _seen.clear()
        _warned.clear()


def _log_once(level: int, key: str, message: str, *args: object) -> None:
    with _lock:
        if key in _warned:
            return
        _warned.add(key)
    _log.log(level, message, *args)


def _warn_once(key: str, message: str, *args: object) -> None:
    _log_once(logging.WARNING, key, message, *args)


def builtin_hosts() -> frozenset[str]:
    """Always-allowed hosts: loopback names plus the public dashboard host."""
    public = urlsplit(DASHBOARD_BASE).hostname
    return LOCAL_HOSTS | ({public.lower()} if public else frozenset())


def normalise_host(header: str | None) -> str | None:
    """Lowercase host without port or trailing dot; None for missing or malformed values."""
    if not header or not header.isascii():
        return None
    text = header.lower()
    if text.startswith("["):
        end = text.find("]")
        host, rest = (text[:end + 1], text[end + 1:]) if end > 0 else ("", "x")
        if not _V6.fullmatch(host):
            return None
    else:
        host, sep, port = text.partition(":")
        rest = sep + port
        host = host.removesuffix(".")
        if not _NAME.fullmatch(host) or len(host) > MAX_HOST_LEN or not all(host.split(".")):
            return None
    if rest and not _valid_port(rest):
        return None
    return host


def _valid_port(rest: str) -> bool:
    return rest.startswith(":") and bool(_PORT.fullmatch(rest[1:])) and 1 <= int(rest[1:]) <= MAX_PORT


def _entry(raw: str) -> str | None:
    suffix = raw.startswith(".")
    host = normalise_host(raw[1:] if suffix else raw)
    if host is None or (suffix and host.startswith("[")):
        return None
    return f".{host}" if suffix else host


def _parse(raw: str | None) -> tuple[frozenset[str], int]:
    """(every valid entry, built-ins included; number of invalid entries)."""
    items = [i for i in _SPLIT.split(raw or "") if i]
    parsed = [_entry(i) for i in items]
    return frozenset(p for p in parsed if p is not None), sum(1 for p in parsed if p is None)


def _config(raw: str | None) -> tuple[frozenset[str], int, int]:
    """(entries without built-ins, number of valid entries, number of invalid entries); warns once on invalid ones."""
    valid, invalid = _parse(raw)
    if invalid:
        _warn_once(f"invalid:{(raw or '')[:80]}", "ALLOWED_HOSTS: ignored %d invalid entries", invalid)
    return valid - builtin_hosts(), len(valid), invalid


def parse_allowed(raw: str | None) -> frozenset[str]:
    """Valid ALLOWED_HOSTS entries (built-ins excluded); invalid ones are ignored and counted in one WARNING."""
    return _config(raw)[0]


def is_allowed(host: str | None, allowed: frozenset[str]) -> bool:
    if host is None:
        return False
    return (host in builtin_hosts() or host in allowed
            or any(a.startswith(".") and host.endswith(a) for a in allowed))


def mode() -> str:
    """HOST_CHECK_MODE: off | log | enforce; unset, empty or unknown -> log (unknown warns once)."""
    value = os.environ.get("HOST_CHECK_MODE", "").strip().lower()
    if value in MODES:
        return value
    if value:
        _warn_once(f"mode:{value[:32]}", "HOST_CHECK_MODE is not off, log or enforce; using log")
    return "log"


def decide(host: str | None, allowed: frozenset[str], current: str, explicit: bool) -> str:
    """allow | log | reject. `enforce` only rejects when ALLOWED_HOSTS has a valid entry (`explicit`)."""
    if current == "off" or is_allowed(host, allowed):
        return "allow"
    if current == "enforce" and explicit:
        return "reject"
    if current == "enforce":
        _warn_once("lockout", "HOST_CHECK_MODE=enforce requested but ALLOWED_HOSTS is empty or has no valid entries; running in log-only mode")
    return "log"


def _log_unknown(host: str | None) -> None:
    label = "<malformed>" if host is None else host[:MAX_LOGGED_LEN]
    with _lock:
        if label in _seen:
            return
        full = len(_seen) >= MAX_LOGGED_HOSTS
        if not full:
            _seen.add(label)
    if full:
        _warn_once("bound", "further unknown hosts are not logged individually")
        return
    _log.warning("Host header not in ALLOWED_HOSTS: %s", label)


def _note_proxy(host: str | None, current: str) -> None:
    """One WARNING when a proxied request (X-Forwarded-For) carries a loopback Host; never changes the decision."""
    if current != "off" and host in LOCAL_HOSTS and request.environ.get("HTTP_X_FORWARDED_FOR"):
        _warn_once("proxy", "requests arrive via a proxy with a loopback Host; the proxy is not forwarding the "
                            "public Host, so the allowlist cannot protect proxied traffic")


def _check() -> Response | None:
    allowed, valid, invalid = _config(os.environ.get("ALLOWED_HOSTS"))
    host = normalise_host(request.environ.get("HTTP_HOST"))
    current = mode()
    effective = "log" if current == "enforce" and not valid else current
    _log_once(logging.INFO, "config", "host check: requested=%s effective=%s valid_entries=%d invalid_entries=%d",
              current, effective, valid, invalid)
    _note_proxy(host, current)
    verdict = decide(host, allowed, current, valid > 0)
    if verdict == "log":
        _log_unknown(host)
    if verdict != "reject":
        return None
    resp = Response(json.dumps({"error": "host_not_allowed"}), status=421, mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def init_app(app: Flask) -> None:
    """Register the check (once) ahead of every other before_request hook."""
    funcs = app.before_request_funcs.setdefault(None, [])
    if _check not in funcs:
        funcs.insert(0, _check)
