"""sync/run-backup.sh end to end against the real school-state CLI (shimmed onto PATH)."""
import json
import os
import signal
import sqlite3
import stat
import subprocess
import time
from contextlib import closing
from pathlib import Path

import pytest

from school_dashboard import db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "sync" / "run-backup.sh"


def _executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "school-state", '#!/usr/bin/env bash\nexec python3 -m school_dashboard.cli "$@"\n')
    # record ntfy pushes instead of sending them
    _executable(bin_dir / "curl", f'#!/usr/bin/env bash\necho "$@" >> {tmp_path}/curl.log\n')
    e = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "PYTHONPATH": str(ROOT),
         "SCHOOL_STATE_DIR": str(state), "SCHOOL_DASHBOARD_ENV": str(tmp_path / "noenv"),
         "NTFY_TOPIC": "t", "HOME": os.environ["HOME"]}
    db.init_db(str(state / "school.db"))
    db.create_item(str(state / "school.db"), child="Ford", title="x")
    (state / "school-state.json").write_text('{"children": {}, "action_items": []}')
    return e, state, tmp_path


def _run(e, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=e, capture_output=True, text=True)


def test_bash_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_creates_private_verified_backup_and_clears_maintenance(env):
    e, state, _ = env
    r = _run(e)
    assert r.returncode == 0, r.stderr
    dirs = list((state / "backups").iterdir())
    assert len(dirs) == 1 and stat.S_IMODE(dirs[0].stat().st_mode) == 0o700
    assert (dirs[0] / "manifest.json").exists()
    with closing(sqlite3.connect(str(dirs[0] / "school.db"))) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not (state / "maintenance.json").exists()
    assert "backup OK" in (state / "backup.log").read_text()


def test_never_backs_up_secrets(env):
    e, state, _ = env
    (state / "gc-token.json").write_text("SECRET")
    (state / "secrets").mkdir()
    (state / "secrets" / "t").write_text("SECRET")
    assert _run(e).returncode == 0
    bdir = next((state / "backups").iterdir())
    assert sorted(p.name for p in bdir.iterdir()) == ["manifest.json", "school-state.json", "school.db"]


def test_prunes_to_backup_keep(env):
    e, state, _ = env
    e["BACKUP_KEEP"] = "2"
    for i in range(4):
        old = state / "backups" / f"2026010{i + 1}T031500000000Z"
        old.mkdir(parents=True)
        (old / "manifest.json").write_text("{}")
    assert _run(e).returncode == 0
    names = sorted(p.name for p in (state / "backups").iterdir())
    assert len(names) == 2 and names[0] == "20260104T031500000000Z"


def test_skips_when_maintenance_already_active_and_leaves_flag(env):
    e, state, tmp = env
    assert subprocess.run(["school-state", "transition", "maintenance", "on"], env=e).returncode == 0
    r = _run(e)
    assert r.returncode == 0
    assert (state / "maintenance.json").exists()
    assert not (state / "backups").exists()
    assert "skipped" in (state / "backup.log").read_text()
    assert "ntfy.sh/t" in (tmp / "curl.log").read_text()  # a skip is alerted, not silent


def _slow_backup_shim(tmp: Path, body: str) -> None:
    """school-state shim whose `transition backup` runs `body`; everything else is the real CLI."""
    _executable(tmp / "bin" / "school-state",
                '#!/usr/bin/env bash\n'
                'if [ "${1:-}" = transition ] && [ "${2:-}" = backup ]; then\n'
                f'{body}\nfi\n'
                'exec python3 -m school_dashboard.cli "$@"\n')


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_signal_mid_backup_clears_our_maintenance_flag(env, sig):
    e, state, tmp = env
    _slow_backup_shim(tmp, f'touch {tmp}/started\nexec sleep 60')
    proc = subprocess.Popen(["bash", str(SCRIPT)], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    for _ in range(100):
        if (tmp / "started").exists():
            break
        time.sleep(0.1)
    assert (tmp / "started").exists() and (state / "maintenance.json").exists()
    proc.send_signal(sig)
    proc.wait(timeout=15)
    assert not (state / "maintenance.json").exists()
    assert "interrupted" in (state / "backup.log").read_text()


def test_foreign_operation_id_flag_is_left_alone(env):
    e, state, tmp = env
    foreign = json.dumps({"operation_id": "deploy-window", "since": "2026-11-09T00:00:00+00:00", "reason": "deploy"})
    _slow_backup_shim(tmp, f"printf '%s' '{foreign}' > {state}/maintenance.json\nexit 0")
    r = _run(e)
    assert r.returncode == 0, r.stderr
    assert json.loads((state / "maintenance.json").read_text())["operation_id"] == "deploy-window"


@pytest.mark.parametrize("raw", ["0", "-2", "many"])
def test_bad_backup_keep_fails_alerts_and_deletes_nothing(env, raw):
    e, state, tmp = env
    e["BACKUP_KEEP"] = raw
    old = state / "backups" / "20260101T031500000000Z"
    old.mkdir(parents=True)
    (old / "manifest.json").write_text("{}")
    r = _run(e)
    assert r.returncode == 2
    assert old.exists() and not (state / "maintenance.json").exists()
    assert "ntfy.sh/t" in (tmp / "curl.log").read_text()


def test_failure_alerts_and_still_clears_maintenance(env):
    e, state, tmp = env
    (state / "school.db").write_bytes(b"this is not a sqlite database" * 50)
    r = _run(e)
    assert r.returncode != 0
    assert not (state / "maintenance.json").exists()
    assert "FAILED" in (state / "backup.log").read_text()
    assert "ntfy.sh/t" in (tmp / "curl.log").read_text()
