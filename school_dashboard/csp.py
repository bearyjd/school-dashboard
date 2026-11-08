"""Report-only Content-Security-Policy header and the counts-only violation sink.

Nothing is enforced. The report body is never read, parsed or logged; only two counters are kept.
"""
import threading

from flask import Blueprint, Flask, current_app, request

# 'unsafe-inline' is needed by the SPA's theme bootstrap script and the legacy page's inline scripts, handlers and
# styles; cdn.jsdelivr.net serves marked + DOMPurify (SRI-pinned) to /legacy.
CSP_REPORT_ONLY = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "manifest-src 'self'; worker-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'self'; report-uri /api/csp-report"
)
REPORT_PATH = "/api/csp-report"
CSP_PAGE_ENDPOINTS = frozenset({"spa", "legacy_index"})
MAX_REPORT_BYTES = 8192

_counts = {"received": 0, "oversize": 0}
_lock = threading.Lock()
bp = Blueprint("csp", __name__)


def counts() -> dict:
    with _lock:
        return dict(_counts)


def reset() -> None:
    with _lock:
        _counts.update(received=0, oversize=0)


@bp.after_app_request
def _add_header(resp):
    if request.endpoint in CSP_PAGE_ENDPOINTS:
        resp.headers["Content-Security-Policy-Report-Only"] = CSP_REPORT_ONLY
    return resp


@bp.route(REPORT_PATH, methods=["POST"])
def report():
    """A request without Content-Length counts as 0 bytes ("received"); the body is not read either way."""
    key = "received" if (request.content_length or 0) <= MAX_REPORT_BYTES else "oversize"
    with _lock:
        _counts[key] += 1
        snapshot = dict(_counts)
    total = snapshot["received"] + snapshot["oversize"]
    if total <= 10 or total % 100 == 0:
        current_app.logger.info("CSP reports so far: received=%d oversize=%d",
                                snapshot["received"], snapshot["oversize"])
    return "", 204


def init_app(app: Flask) -> None:
    app.register_blueprint(bp)
