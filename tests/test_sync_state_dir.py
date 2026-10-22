"""
STATE_DIR derivation shared by every sync/*.sh script.

Mirrors school_dashboard/paths.py: SCHOOL_STATE_DIR, else the parent of
SCHOOL_STATE_PATH, else /app/state.  As in test_wrapper_scripts.py, we run the
real derivation lines (extracted from each script) in a bash snippet rather
than running the scripts, which need live credentials and services.
"""
import subprocess
import pytest

SYNC_SCRIPTS = [
    "sync/run-digest.sh",
    "sync/run-weekly.sh",
    "sync/gc-scrape.sh",
    "sync/school-sync.sh",
]


def _bash(snippet: str, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = ["bash", "-euo", "pipefail", "-c", snippet]
    return subprocess.run(cmd, capture_output=True, text=True, env=env or {})


def _state_dir_prelude(script: str) -> str:
    with open(script) as f:
        lines = [ln for ln in f.read().splitlines() if ln.startswith("STATE_DIR=")]
    assert len(lines) == 2, f"{script}: expected 2 STATE_DIR= lines, got {lines}"
    return "\n".join(lines)


def _state_dir(script: str, env: dict) -> str:
    result = _bash(_state_dir_prelude(script) + '\necho "$STATE_DIR"', env=env)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize("script", SYNC_SCRIPTS)
def test_defaults_to_app_state(script):
    assert _state_dir(script, {}) == "/app/state"


@pytest.mark.parametrize("script", SYNC_SCRIPTS)
def test_state_dir_env_wins(script):
    env = {"SCHOOL_STATE_DIR": "/data/s", "SCHOOL_STATE_PATH": "/other/school-state.json"}
    assert _state_dir(script, env) == "/data/s"


@pytest.mark.parametrize("script", SYNC_SCRIPTS)
def test_falls_back_to_parent_of_state_path(script):
    env = {"SCHOOL_STATE_PATH": "/custom/path/school-state.json"}
    assert _state_dir(script, env) == "/custom/path"


@pytest.mark.parametrize("script", SYNC_SCRIPTS)
def test_no_inline_state_file_defaults(script):
    """No code line may hardcode /app/state/<file>; only STATE_DIR does."""
    with open(script) as f:
        offenders = [
            ln for ln in f.read().splitlines()
            if "/app/state/" in ln and not ln.lstrip().startswith("#")
        ]
    assert offenders == []


@pytest.mark.parametrize("script", SYNC_SCRIPTS)
def test_maintenance_gate_still_requires_exit_10(script):
    with open(script) as f:
        text = f.read()
    assert "school-state transition maintenance check" in text
    assert "-ne 10" in text
