"""docker/entrypoint.sh must serve the app with gunicorn: exactly one worker process (in-process sync state)."""
import re
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parent.parent / "docker" / "entrypoint.sh"


def _command_lines() -> list[str]:
    return [ln.strip() for ln in ENTRYPOINT.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _server_line() -> str:
    lines = [ln for ln in _command_lines() if "gunicorn" in ln]
    assert len(lines) == 1, lines
    return lines[0]


def test_flask_dev_server_not_used():
    assert not any(re.search(r"\bflask\b.*\brun\b", ln) for ln in _command_lines())


def test_gunicorn_one_worker_with_threads():
    line = _server_line()
    assert line.startswith("exec "), "exec so SIGTERM reaches gunicorn"
    assert re.search(r"(?:-w|--workers)[ =]1\b", line)
    m = re.search(r"--threads[ =](\d+)", line)
    assert m and int(m.group(1)) >= 2
    assert re.search(r"--timeout[ =](\d+)", line)
    assert "0.0.0.0:5000" in line
    assert line.rstrip().endswith("web.app:app")


def test_gunicorn_in_server_extra():
    pyproject = (ENTRYPOINT.parent.parent / "pyproject.toml").read_text()
    assert re.search(r'server\s*=\s*\[[^\]]*"gunicorn', pyproject)
