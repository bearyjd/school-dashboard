"""A broken assignments CLI must not break the other school-state commands."""
import json
import sys
from unittest.mock import patch

import pytest


def _main(*args):
    from school_dashboard import cli
    with patch.object(sys, "argv", ["school-state", *args]), pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.fixture
def broken(monkeypatch):
    monkeypatch.setitem(sys.modules, "school_dashboard.assignments.cli", None)


def test_other_commands_still_work(broken, state_env, capsys):
    state_file = state_env / "school-state.json"
    state_file.write_text(json.dumps({"children": {}}))
    code = _main("show", "--state-file", str(state_file))
    assert code in (None, 0)


def test_assignments_exits_1_with_a_clear_message(broken, state_env, capsys, caplog):
    assert _main("assignments", "report") == 1
    err = capsys.readouterr().err
    assert "assignments" in err and "unavailable" in err
    assert "ImportError" in caplog.text


def test_assignments_works_when_importable(state_env, capsys):
    assert _main("assignments", "report") == 3  # no database yet: the real handler ran
