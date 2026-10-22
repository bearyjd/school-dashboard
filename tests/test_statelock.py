"""State file lock + atomic save."""
import argparse
import json
import stat
import threading

import pytest

from school_dashboard import cli, schoology_reset, state, statelock


def _add_args(sf, n):
    return argparse.Namespace(state_file=str(sf), child="Ford", summary=f"task {n}", due=None,
                              source="manual", type="task")


def test_concurrent_action_add_loses_nothing(state_env):
    sf = state_env / "school-state.json"
    state.save(state._empty_state(), str(sf))
    errors = []

    def work(n):
        try:
            cli.cmd_action_add(_add_args(sf, n))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    items = json.loads(sf.read_text())["action_items"]
    assert sorted(i["summary"] for i in items) == sorted(f"task {n}" for n in range(12))


def test_lock_timeout_message(tmp_path):
    sf = tmp_path / "s.json"
    with statelock.state_lock(sf):
        with pytest.raises(statelock.StateLockTimeout, match="lock"):
            with statelock.state_lock(sf, timeout=0.2):
                pass
    with statelock.state_lock(sf, timeout=0.2):  # released
        pass


def test_cli_exits_1_on_lock_timeout(state_env, monkeypatch, capsys):
    sf = state_env / "school-state.json"
    monkeypatch.setattr(statelock, "LOCK_TIMEOUT", 0.2)
    with statelock.state_lock(sf):
        with pytest.raises(SystemExit) as exc:
            cli.cmd_action_add(_add_args(sf, 1))
        assert exc.value.code == 1
        args = argparse.Namespace(schoology_command="reset", sgy_file=sf, archive_dir=None, max_age_hours=24,
                                  dry_run=False, state_file=str(sf), allow_warnings=False, json=False)
        with pytest.raises(SystemExit) as exc2:
            schoology_reset.cmd_schoology(args)
        assert exc2.value.code == 1
    assert "lock" in capsys.readouterr().err.lower()


def test_save_keeps_mode_and_leaves_no_temp_files(state_env):
    sf = state_env / "school-state.json"
    state.save(state._empty_state(), str(sf))
    assert stat.S_IMODE(sf.stat().st_mode) == 0o600
    sf.chmod(0o640)
    state.save(state.load(str(sf)), str(sf))
    assert stat.S_IMODE(sf.stat().st_mode) == 0o640
    assert not [p for p in state_env.iterdir() if p.name.endswith(".tmp")]
