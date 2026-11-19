"""`school-state assignments alias add|list`: what is stored must be exactly what the ingest reads."""
import json

import pytest

from recon.ingest_support import WED, build_results, make_db
from recon.report_support import cli, db_with, sgy


def _rows(path, kid="Alex"):
    from school_dashboard.assignments import repo, schema
    conn = schema.connect(path)
    try:
        return repo.class_settings_rows(conn, kid)
    finally:
        conn.close()


@pytest.fixture
def path(state_env):
    return db_with(state_env, [sgy("Essay Draft", "Language Arts", "s1")])


def test_add_stores_alias_on_the_canonical_class_row_as_a_json_list(state_env, path):
    r = cli(state_env, "alias", "add", "--kid", "Alex", "--from", "ELA", "--to", "Language Arts")
    assert r.returncode == 0, r.stderr
    rows = _rows(path)
    assert [(k, json.loads(a or "[]")) for k, _, a in rows] == [("language arts", ["ELA"])]


def test_ingest_reads_exactly_what_the_cli_wrote(state_env, path):
    from school_dashboard.assignments.ingest_kid import parse_aliases
    cli(state_env, "alias", "add", "--kid", "Alex", "--from", "ELA", "--to", "Language Arts")
    assert parse_aliases(_rows(path)) == {"ELA": "language arts"}


def test_alias_makes_the_next_ingest_merge_the_two_sources(state_env):
    from school_dashboard.assignments import ingest
    p = make_db(state_env, {"seed": [sgy("Essay Draft", "Language Arts", "s1")]})
    assert cli(state_env, "alias", "add", "--kid", "Alex", "--from", "ELA", "--to", "Language Arts").returncode == 0
    run = {"ps": {"Alex": {"upcoming": [{"title": "Essay Draft", "course": "ELA", "due": "2026-11-20"}]}}}
    results, hashes = build_results(run)
    rep = ingest.run(p, results, file_hashes=hashes, now=WED, no_school_rows=[])
    assert rep.ok and rep.kids[0].merged == 1 and rep.kids[0].created == 0


def test_add_is_idempotent_and_keeps_other_settings_and_aliases(state_env):
    p = make_db(state_env, {"seed": [sgy("Essay Draft", "Language Arts", "s1")],
                            "class_settings": [{"kid": "Alex", "class_key": "language arts",
                                                "grace_school_days": 4, "paper_only": 1, "aliases": ["LA"]}]})
    for _ in range(2):
        assert cli(state_env, "alias", "add", "--kid", "Alex", "--from", "ELA", "--to", "Language Arts").returncode == 0
    (key, grace, aliases), = _rows(p)
    assert (key, grace, json.loads(aliases or "[]")) == ("language arts", 4, ["LA", "ELA"])


def test_add_validates_kid_and_names(state_env, path):
    base = ["alias", "add"]
    assert cli(state_env, *base, "--kid", "Nobody", "--from", "ELA", "--to", "Language Arts").returncode == 2
    assert cli(state_env, *base, "--kid", "Alex", "--from", "  ", "--to", "Language Arts").returncode == 2
    assert cli(state_env, *base, "--kid", "Alex", "--from", "ELA", "--to", "").returncode == 2
    assert cli(state_env, *base, "--kid", "Alex", "--from", "language arts", "--to", "Language Arts").returncode == 2
    assert _rows(path) == []


def test_add_on_an_unmigrated_database_exits_3(state_env):
    assert cli(state_env, "alias", "add", "--kid", "Alex", "--from", "A", "--to", "B").returncode == 3


def test_add_works_with_maintenance_off_and_touches_no_assignment(state_env, path):
    from recon.conftest import table_sha
    before = table_sha(path, "assignments"), table_sha(path, "source_links")
    assert cli(state_env, "alias", "add", "--kid", "Alex", "--from", "ELA", "--to", "Language Arts").returncode == 0
    assert (table_sha(path, "assignments"), table_sha(path, "source_links")) == before


def test_list_shows_aliases_and_filters_by_kid(state_env):
    make_db(state_env, {"seed": [sgy("Essay Draft", "Language Arts", "s1"),
                                 {**sgy("Poster", "Art", "s9"), "kid": "Sam"}]})
    cli(state_env, "alias", "add", "--kid", "Alex", "--from", "ELA", "--to", "Language Arts")
    cli(state_env, "alias", "add", "--kid", "Sam", "--from", "Studio", "--to", "Art")
    everyone = cli(state_env, "alias", "list", "--json")
    rows = json.loads(everyone.stdout)
    assert {(r["kid"], r["alias"], r["class"]) for r in rows} == {
        ("Alex", "ELA", "language arts"), ("Sam", "Studio", "art")}
    only = json.loads(cli(state_env, "alias", "list", "--kid", "Sam", "--json").stdout)
    assert [r["kid"] for r in only] == ["Sam"]
    text = cli(state_env, "alias", "list")
    assert text.returncode == 0 and "ELA" in text.stdout and "Studio" in text.stdout
