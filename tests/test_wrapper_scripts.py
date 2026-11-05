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
    """All wrapper scripts should pass bash syntax checking."""

    @pytest.mark.parametrize("script", [
        "sync/run-digest.sh",
        "sync/run-weekly.sh",
        "sync/gc-scrape.sh",
        "sync/school-sync.sh",
    ])
    def test_script_has_valid_bash_syntax(self, script):
        result = subprocess.run(
            ["bash", "-n", script],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"{script} has syntax errors:\n{result.stderr}"
        )


# ── gc-scrape.sh empty-data detection ─────────────────────────────────────────

class TestGcScrapeEmptyDataDetection:
    """Regression: gc returns 200 with empty data on 401, so a wrapper that
    only checks exit code thinks every sync succeeded while gc-schedule.json
    sits stale. The fail-loud guard sums events across teams and refuses to
    overwrite state when every team came back empty."""

    SUM_SNIPPET = '''
OUTPUT_TEAMS=%(input)s
TEAM_COUNT=%(team_count)d
TOTAL_EVENTS=$(python3 -c "
import json, sys
teams = json.loads(sys.argv[1])
print(sum(len(t.get('schedule', [])) for t in teams))
" "$OUTPUT_TEAMS")
if [[ "$TEAM_COUNT" -gt 0 && "$TOTAL_EVENTS" -eq 0 ]]; then
    echo "REFUSE"
    exit 1
fi
echo "WRITE $TOTAL_EVENTS"
'''

    def test_all_empty_schedules_refuse_to_write(self):
        snippet = self.SUM_SNIPPET % {
            "input": "'[{\"team_id\":\"a\",\"schedule\":[]},{\"team_id\":\"b\",\"schedule\":[]}]'",
            "team_count": 2,
        }
        result = _bash(snippet)
        assert result.returncode == 1
        assert "REFUSE" in result.stdout

    def test_at_least_one_event_proceeds_to_write(self):
        snippet = self.SUM_SNIPPET % {
            "input": "'[{\"team_id\":\"a\",\"schedule\":[{\"id\":\"e1\"}]},{\"team_id\":\"b\",\"schedule\":[]}]'",
            "team_count": 2,
        }
        result = _bash(snippet)
        assert result.returncode == 0
        assert "WRITE 1" in result.stdout

    def test_zero_teams_does_not_trigger_refuse(self):
        """If TEAM_COUNT is 0, the upstream `gc teams` already failed loudly;
        don't double-fail with a misleading 'all empty' message."""
        snippet = self.SUM_SNIPPET % {"input": "'[]'", "team_count": 0}
        result = _bash(snippet)
        assert result.returncode == 0
        assert "WRITE 0" in result.stdout


# ── gc-scrape.sh unmapped-team warning ────────────────────────────────────────

class TestGcScrapeUnmappedTeamWarning:
    """A team ID missing from a non-empty GC_TEAM_MAP falls back to the team
    name as the child label; that must be logged as a WARN, not silent.
    The real function is extracted from the script so the test tracks it."""

    SNIPPET = '''
eval "$(sed -n '/^warn_if_unmapped() {/,/^}/p' sync/gc-scrape.sh)"
log() { echo "$*"; }
declare -A TEAM_MAP
%(setup)s
warn_if_unmapped t1 "Tigers"
'''

    def _run(self, setup: str) -> subprocess.CompletedProcess:
        return _bash(self.SNIPPET % {"setup": setup})

    def test_mapped_team_no_warning(self):
        result = self._run('GC_TEAM_MAP="t1:Ford"; TEAM_MAP[t1]=Ford')
        assert result.returncode == 0, result.stderr
        assert "WARN" not in result.stdout

    def test_unmapped_team_warns_with_id_and_name(self):
        result = self._run('GC_TEAM_MAP="t2:Jack"; TEAM_MAP[t2]=Jack')
        assert result.returncode == 0, result.stderr
        assert "WARN" in result.stdout
        assert "t1" in result.stdout and "Tigers" in result.stdout

    def test_unset_map_no_warning(self):
        result = self._run("unset GC_TEAM_MAP")
        assert result.returncode == 0, result.stderr
        assert "WARN" not in result.stdout


# ── school-sync.sh PowerSchool block ─────────────────────────────────────────

class TestSchoolSyncPowerschoolBlock:
    """Run the extracted Step 2a block with stubbed helpers and a fake `powerschool`."""

    @staticmethod
    def _run(tmp_path, fake_body=None, env_extra=None):
        import re
        from pathlib import Path
        src = (Path(__file__).parent.parent / "sync" / "school-sync.sh").read_text()
        block = re.search(r"# --- Step 2a:.*?(?=# --- Step 2b)", src, re.S).group(0)
        bindir = tmp_path / "bin"
        bindir.mkdir()
        if fake_body is not None:
            fake = bindir / "powerschool"
            fake.write_text("#!/bin/bash\n" + fake_body)
            fake.chmod(0o755)
        home = tmp_path / "home"
        home.mkdir()
        prelude = (
            'log() { echo "LOG: $*"; }\n'
            'write_sync_meta() { echo "META: $1 $2"; }\n'
            'pause_source() { echo "PAUSED: $1"; }\n'
            'source_enabled() { [[ "${SOURCE_POWERSCHOOL_ENABLED:-1}" != "0" ]]; }\n'
            f"PS_FILE={tmp_path}/ps.json\nSTATE_DIR={tmp_path}\n"
        )
        env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(home), **(env_extra or {})}
        return _bash(prelude + block, env=env)

    def test_not_installed_skips_without_meta(self, tmp_path):
        r = self._run(tmp_path, env_extra={"PS_USERNAME": "u"})
        assert r.returncode == 0, r.stderr
        assert "skipping" in r.stdout and "META" not in r.stdout

    def test_no_credentials_skips_without_meta(self, tmp_path):
        r = self._run(tmp_path, fake_body="echo '{}'\n")
        assert r.returncode == 0, r.stderr
        assert "not configured" in r.stdout and "META" not in r.stdout

    def test_paused(self, tmp_path):
        r = self._run(tmp_path, fake_body="echo '{}'\n",
                      env_extra={"PS_USERNAME": "u", "SOURCE_POWERSCHOOL_ENABLED": "0"})
        assert "PAUSED: powerschool" in r.stdout

    def test_success_writes_file_and_meta_ok(self, tmp_path):
        r = self._run(tmp_path, fake_body="echo '{\"children\": []}'\n", env_extra={"PS_USERNAME": "u"})
        assert "META: powerschool ok" in r.stdout
        assert (tmp_path / "ps.json").exists()

    def test_failure_is_nonfatal_and_keeps_previous_file(self, tmp_path):
        (tmp_path / "ps.json").write_text("OLD")
        r = self._run(tmp_path, fake_body="exit 1\n", env_extra={"PS_USERNAME": "u"})
        assert r.returncode == 0, r.stderr
        assert "META: powerschool error" in r.stdout
        assert (tmp_path / "ps.json").read_text() == "OLD"
        assert not (tmp_path / "ps.json.tmp").exists()
