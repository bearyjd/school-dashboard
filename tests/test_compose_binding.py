"""docker-compose.yml port publishing: loopback by default, overridable (S4b)."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "docker-compose.yml").read_text()
PORTS = yaml.safe_load(TEXT)["services"]["dashboard"]["ports"]
EXPECTED = "${DASHBOARD_BIND_ADDR:-127.0.0.1}:${DASHBOARD_PORT:-5000}:5000"


def test_single_port_mapping_with_loopback_default():
    assert PORTS == [EXPECTED]


def test_address_and_host_port_overridable_container_port_fixed():
    m = re.fullmatch(r"\$\{(\w+):-([^}]*)\}:\$\{(\w+):-(\d+)\}:(\d+)", PORTS[0])
    assert m and m.groups() == ("DASHBOARD_BIND_ADDR", "127.0.0.1", "DASHBOARD_PORT", "5000", "5000")


def test_nothing_publishes_all_interfaces_literally():
    assert "0.0.0.0" not in TEXT
    assert '"5000:5000"' not in TEXT


def test_comment_says_which_env_file_compose_reads():
    assert "next to docker-compose.yml" in TEXT and "config/env" in TEXT


def test_gunicorn_bind_unchanged():
    assert "-b 0.0.0.0:5000" in (ROOT / "docker" / "entrypoint.sh").read_text()
