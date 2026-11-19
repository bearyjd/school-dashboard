"""Operation-id guarded maintenance off + `on --json` (used by sync/run-backup.sh)."""
import json
from unittest.mock import patch

from school_dashboard import cli, maintenance


def run(*argv):
    with patch("sys.argv", ["school-state", *argv]):
        try:
            cli.main()
        except SystemExit as e:
            return e.code if e.code is not None else 0
    return 0


def test_disable_with_matching_operation_id_removes(state_env):
    info = maintenance.enable("t")
    assert maintenance.disable(operation_id=info["operation_id"]) is True
    assert not maintenance.is_active()


def test_disable_with_foreign_operation_id_leaves_flag(state_env):
    maintenance.enable("t")
    assert maintenance.disable(operation_id="someone-else") is False
    assert maintenance.is_active()


def test_disable_without_id_still_unconditional(state_env):
    maintenance.enable("t")
    assert maintenance.disable() is True


def test_cli_on_json_reports_operation_id_and_already_active(state_env, capsys):
    assert run("transition", "maintenance", "on", "--json") == 0
    first = json.loads(capsys.readouterr().out)
    assert first["already_active"] is False and first["operation_id"] == maintenance.status()["operation_id"]
    assert run("transition", "maintenance", "on", "--json") == 0
    second = json.loads(capsys.readouterr().out)
    assert second["already_active"] is True and second["operation_id"] == first["operation_id"]


def test_cli_off_operation_id_guard(state_env):
    info = maintenance.enable("t")
    assert run("transition", "maintenance", "off", "--operation-id", "nope") == 0
    assert maintenance.is_active()
    assert run("transition", "maintenance", "off", "--operation-id", info["operation_id"]) == 0
    assert not maintenance.is_active()
