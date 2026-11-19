"""In-process coverage for the alias module and the report/alias CLI handlers (the subprocess tests do not count)."""
import json
import sys
from unittest.mock import patch

import pytest

from recon.ingest_support import make_db
from recon.report_support import ps, sgy


def _main(*args):
    from school_dashboard import cli
    with patch.object(sys, "argv", ["school-state", "assignments", *args]), pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.fixture
def conn(state_env):
    from school_dashboard.assignments import schema
    path = make_db(state_env, {"seed": [sgy("Essay Draft", "Language Arts", "s1"), ps("Essay Draft", "ELA", "p1")]})
    c = schema.connect(path)
    yield c
    c.close()


def test_add_and_list_round_trip_and_bad_json_is_replaced(conn):
    from school_dashboard.assignments import aliases
    aliases.add(conn, kid="Alex", from_name="ELA", to_name="Language Arts")
    conn.execute("UPDATE class_settings SET aliases = 'not json'")
    aliases.add(conn, kid="Alex", from_name="LA", to_name="Language Arts")
    assert [r.alias for r in aliases.list_aliases(conn)] == ["LA"]
    assert aliases.list_aliases(conn, "Sam") == []


def test_add_rejects_bad_input(conn):
    from school_dashboard.assignments import aliases
    for kw in ({"from_name": "", "to_name": "X"}, {"from_name": "X", "to_name": " "},
               {"from_name": "ela", "to_name": "ELA"}):
        with pytest.raises(aliases.AliasError):
            aliases.add(conn, kid="Alex", **kw)
    with pytest.raises(aliases.AliasError):
        aliases.add(conn, kid="Zed", from_name="A", to_name="B")


def test_cli_handlers_in_process(conn, capsys):
    assert _main("alias", "add", "--kid", "Alex", "--from", "ELA", "--to", "Language Arts") == 0
    assert "ELA -> language arts" in capsys.readouterr().out
    assert _main("alias", "list", "--json") == 0
    assert json.loads(capsys.readouterr().out)[0]["alias"] == "ELA"
    assert _main("alias", "list") == 0
    assert _main("alias", "add", "--kid", "Zed", "--from", "A", "--to", "B") == 2
    assert _main("report", "--json", "--redact") == 0
    out = capsys.readouterr()
    assert "Essay" not in out.out and "1 unmatched pairs" in out.err
    assert _main("report") == 0


def test_cli_errors_map_to_exit_codes(state_env, capsys):
    assert _main("report") == 3          # no database yet
    assert _main("alias", "list") == 3
    capsys.readouterr()
