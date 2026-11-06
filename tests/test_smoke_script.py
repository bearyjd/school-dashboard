"""scripts/smoke-live.sh against a stub HTTP server: PASS and FAIL paths."""
import json
import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "smoke-live.sh"


def _now(hours_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _routes(**overrides) -> dict:
    routes = {
        "/api/dashboard": {"children": [{"name": "Secret Kid"}], "has_data": {}, "powerschool": {}},
        "/api/readiness": {"checklist": {}},
        "/api/sync/status": {"running": False},
        "/api/sync/meta": {"ps": {"last_run": _now(1), "last_result": "ok"},
                           "gc": {"last_run": _now(100), "last_result": "paused"}},
    }
    routes.update(overrides)
    return routes


SEEN_HEADERS: list = []


@pytest.fixture
def stub_server():
    servers = []
    SEEN_HEADERS.clear()

    def start(routes: dict) -> str:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                SEEN_HEADERS.append(dict(self.headers))
                if self.path not in routes:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = routes[self.path]
                payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _run(base_url: str, **env):
    clean = {k: v for k, v in os.environ.items() if k not in ("SYNC_TOKEN", "MAX_AGE_HOURS", "BASE_URL")}
    full_env = {**clean, "BASE_URL": base_url, **env}
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=full_env, timeout=60)


def test_pass_path(stub_server):
    proc = _run(stub_server(_routes()), SYNC_TOKEN="tok-do-not-print")
    assert proc.returncode == 0, proc.stdout
    assert "SMOKE: PASS" in proc.stdout
    assert "FAIL" not in proc.stdout
    assert "Secret Kid" not in proc.stdout + proc.stderr
    assert "tok-do-not-print" not in proc.stdout + proc.stderr
    assert all(h.get("X-Sync-Token") == "tok-do-not-print" for h in SEEN_HEADERS) and SEEN_HEADERS


def test_no_token_header_without_sync_token(stub_server):
    assert _run(stub_server(_routes())).returncode == 0
    assert SEEN_HEADERS and not any("X-Sync-Token" in h for h in SEEN_HEADERS)


def test_refuses_token_over_plain_http_to_remote_host():
    proc = _run("http://example.invalid:5000", SYNC_TOKEN="tok")
    assert proc.returncode == 2 and "tok" not in proc.stdout + proc.stderr.replace("SYNC_TOKEN", "")


def test_non_numeric_max_age_is_usage_error(stub_server):
    assert _run(stub_server(_routes()), MAX_AGE_HOURS="abc").returncode == 2


def test_empty_sync_meta_fails(stub_server):
    proc = _run(stub_server(_routes(**{"/api/sync/meta": {}})))
    assert proc.returncode == 1 and "SMOKE: FAIL" in proc.stdout


def test_bad_container_name_rejected():
    proc = subprocess.run(["bash", str(SCRIPT), "--container", "-evil"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2


@pytest.mark.parametrize("overrides,expected", [
    ({"/api/dashboard": {"children": [], "has_data": {}, "powerschool": {}}}, "children non-empty"),
    ({"/api/dashboard": {"children": [{"name": "x"}]}}, "has_data/powerschool keys"),
    ({"/api/readiness": "not json"}, "GET /api/readiness"),
    ({"/api/sync/meta": {"ps": {"last_run": _now(48), "last_result": "ok"}}}, "sync_meta ps"),
    ({"/api/sync/meta": {"ps": {"last_run": _now(1), "last_result": "error"}}}, "sync_meta ps"),
])
def test_fail_paths(stub_server, overrides, expected):
    proc = _run(stub_server(_routes(**overrides)))
    assert proc.returncode == 1, proc.stdout
    assert "SMOKE: FAIL" in proc.stdout
    assert any(expected in line and "FAIL" in line for line in proc.stdout.splitlines()), proc.stdout


def test_missing_endpoint_and_max_age_override(stub_server):
    routes = _routes()
    del routes["/api/sync/status"]
    proc = _run(stub_server(routes))
    assert proc.returncode == 1
    assert "HTTP 404" in proc.stdout
    stale = _routes(**{"/api/sync/meta": {"ps": {"last_run": _now(48), "last_result": "ok"}}})
    assert _run(stub_server(stale), MAX_AGE_HOURS="72").returncode == 0


def test_server_down_fails():
    proc = _run("http://127.0.0.1:1")
    assert proc.returncode == 1
    assert "HTTP 000" in proc.stdout
