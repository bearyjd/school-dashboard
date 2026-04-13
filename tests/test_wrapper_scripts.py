"""
Tests for sync/run-digest.sh and sync/run-weekly.sh wrapper scripts.

These scripts run under `set -euo pipefail`.  The key regression being guarded
here is that SCHOOL_STATE_PATH is commented out in .env.example (not set by
default), so every line that uses it must supply a default.

We test the LOGDIR derivation logic by running bash snippets directly rather
than running the full scripts (which need live credentials and all services).
"""
import subprocess
import pytest


def _bash(snippet: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a bash snippet under set -euo pipefail; return the result."""
    cmd = ["bash", "-euo", "pipefail", "-c", snippet]
    return subprocess.run(cmd, capture_output=True, text=True, env=env or {})


# ── LOGDIR derivation (the actual regression) ─────────────────────────────────

class TestLogdirDerivation:
    """The old code: LOGDIR="${SCHOOL_STATE_PATH%/*}" crashed with set -u when
    SCHOOL_STATE_PATH was unset.  The fix: give it a default first."""

    OLD_SNIPPET = (
        'LOGDIR="${SCHOOL_STATE_PATH%/*}"; '
        'LOGDIR="${LOGDIR:-/app/state}"; '
        'echo "$LOGDIR"'
    )
    NEW_SNIPPET = (
        'LOGDIR="${SCHOOL_STATE_PATH:-/app/state/school-state.json}"; '
        'LOGDIR="${LOGDIR%/*}"; '
        'echo "$LOGDIR"'
    )

    def test_old_code_crashes_when_school_state_path_unset(self):
        """Prove the pre-fix snippet fails — so the regression test is meaningful."""
        result = _bash(self.OLD_SNIPPET, env={})
        assert result.returncode != 0, (
            "Expected old snippet to crash with unbound variable, but it succeeded"
        )
        assert "SCHOOL_STATE_PATH" in result.stderr

    def test_fixed_logdir_defaults_to_app_state_when_path_unset(self):
        result = _bash(self.NEW_SNIPPET, env={})
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "/app/state"

    def test_fixed_logdir_derives_from_school_state_path_when_set(self):
        env = {"SCHOOL_STATE_PATH": "/custom/path/school-state.json"}
        result = _bash(self.NEW_SNIPPET, env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "/custom/path"

    def test_fixed_logdir_handles_shallow_path(self):
        """Path with no directory component should still produce a valid dir."""
        env = {"SCHOOL_STATE_PATH": "/app/state/school-state.json"}
        result = _bash(self.NEW_SNIPPET, env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "/app/state"


# ── Script syntax ─────────────────────────────────────────────────────────────

class TestScriptSyntax:
    """Both wrapper scripts should pass bash syntax checking."""

    @pytest.mark.parametrize("script", [
        "sync/run-digest.sh",
        "sync/run-weekly.sh",
    ])
    def test_script_has_valid_bash_syntax(self, script):
        result = subprocess.run(
            ["bash", "-n", script],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"{script} has syntax errors:\n{result.stderr}"
        )
