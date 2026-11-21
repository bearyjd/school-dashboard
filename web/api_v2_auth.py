"""Write credential for /api/v2: header `X-Recon-Token` against env `RECON_WRITE_TOKEN` (no Flask objects here)."""
from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass

from school_dashboard.assignments import flagsenv
from school_dashboard.rate_limit import client_key, trusted_client_ip, trusted_proxy_count

logger = logging.getLogger(__name__)
HEADER = "X-Recon-Token"
MIN_TOKEN_LEN = 32
LOGGED: set[str] = set()  # warnings already emitted (one WARNING per condition)


@dataclass(frozen=True)
class Rejection:
    status: int
    code: str
    retry_after: int | None = None


def _warn_once(key: str, message: str) -> None:
    if key not in LOGGED:
        LOGGED.add(key)
        logger.warning(message)


def _token() -> str:
    token = os.environ.get("RECON_WRITE_TOKEN", "")
    return token if len(token) >= MIN_TOKEN_LEN else ""


def mode() -> str:
    """`open` (explicit opt-out), `token` or `unconfigured`; never the token itself."""
    if flagsenv.enabled("RECON_WRITE_OPEN"):
        return "open"
    return "token" if _token() else "unconfigured"


def _limiter():  # the limiter /api/sync uses; imported late because web.app imports this blueprint
    from web import app as webapp
    return webapp._sync_failures


def check_write(provided: str, forwarded: list[str], remote_addr: str | None) -> Rejection | None:
    """None when the write may proceed; otherwise why not."""
    current = mode()
    if current == "open":
        _warn_once("open", "RECON_WRITE_OPEN is set: /api/v2 writes need no token (trusted private network only)")
        return None
    if current == "unconfigured":
        _warn_once("unconfigured", "RECON_WRITE_TOKEN is missing or not 32+ characters: /api/v2 writes are disabled")
        return Rejection(501, "write_token_not_configured")
    client = client_key(trusted_client_ip(remote_addr, ", ".join(forwarded),
                                          trusted_proxy_count(os.environ.get("TRUSTED_PROXY_COUNT"))))
    if provided and hmac.compare_digest(provided.encode(), _token().encode()):
        _limiter().reset(client)  # a correct token is never blocked by earlier failures
        return None
    wait = _limiter().fail(client)
    return Rejection(429, "too_many_attempts", wait) if wait else Rejection(401, "bad_token")
