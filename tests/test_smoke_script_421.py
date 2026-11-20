"""smoke-live.sh prints an ALLOWED_HOSTS hint on HTTP 421 (S4b)."""
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "smoke-live.sh"


def test_421_gets_a_hint():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(421)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env = {k: v for k, v in os.environ.items() if k not in ("SYNC_TOKEN", "BASE_URL")}
        env["BASE_URL"] = f"http://127.0.0.1:{srv.server_port}"
        p = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60)
    finally:
        srv.shutdown()
        srv.server_close()
    assert p.returncode == 1
    assert "HTTP 421" in p.stdout and "ALLOWED_HOSTS" in p.stdout
