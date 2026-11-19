"""A broken assignments CLI must not break the other school-state commands."""
import json
import sys
from unittest.mock import patch

import pytest


def _main(*args):
    from school_dashboard import cli
    with patch.object(sys, "argv", ["school-state", *args]):
        try:
            cli.main()
        except SystemExit as exc:
            return exc.code
    return 0


@pytest.fixture
def broken(monkeypatch):
    import school_dashboard.assignments as pkg
    monkeypatch.delattr(pkg, "cli", raising=False)  # `from pkg import cli` prefers the attribute
    monkeypatch.setitem(sys.modules, "school_dashboard.assignments.cli", None)


def test_other_commands_still_work(broken, state_env, capsys):
    state_file = state_env / "school-state.json"
    state_file.write_text(json.dumps({"children": {}, "action_items": []}))
    code = _main("--state-file", str(state_file), "show")
    assert code in (None, 0)


def test_assignments_exits_1_with_a_clear_message(broken, state_env, capsys, caplog):
    assert _main("assignments", "report") == 1
    err = capsys.readouterr().err
    assert "assignments" in err and "unavailable" in err
    assert "ModuleNotFoundError" in caplog.text


def test_assignments_works_when_importable(state_env, capsys):
    assert _main("assignments", "report") == 3  # no database yet: the real handler ran
