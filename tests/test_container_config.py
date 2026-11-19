"""File-content checks for the container healthcheck and docker log limits."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_has_healthcheck():
    text = (ROOT / "Dockerfile").read_text()
    m = re.search(r"^HEALTHCHECK\b(.*?)(?=^\S)", text + "\nEND", re.S | re.M)
    assert m, "Dockerfile has no HEALTHCHECK"
    hc = m.group(0)
    for needle in ("--interval=60s", "--timeout=10s", "--start-period=60s", "--retries=3",
                   "127.0.0.1:5000/api/sync/status"):
        assert needle in hc
    assert "TOKEN" not in hc.upper()


def test_compose_has_log_limits_and_restart():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "restart: unless-stopped" in text
    assert re.search(r'logging:\s*\n\s+driver: json-file\s*\n\s+options:\s*\n\s+max-size: "10m"\s*\n\s+max-file: "3"', text)
    assert "/app/web" not in text
