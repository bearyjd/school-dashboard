"""Contract: `powerschool summary --json` (tests/fixtures/contracts/powerschool.json).

Consumers and the keys they read:
  state.update_from_ps_file   scraped_at, warnings, children[].{name, missing, late, upcoming,
                              warnings}; each item {course, title, due, category, score}
  overview_items._powerschool_rows   item.{title, course, due, category, score}
  powerschool.format_block / format_counts   item.{course, title, due}
Provenance caveat: vendor/powerschool-scrape is not checked out in CI; this shape follows
powerschool.py's documented contract (see the fixtures README).
"""
import logging

import pytest
from contract_support import (TODAY, build_state, consumers, load, mutate, renamed,
                              set_roster)

from school_dashboard import powerschool, state

ITEM_KEYS = ("course", "title", "due", "category", "score")


@pytest.fixture
def env(state_env, tmp_path, monkeypatch):
    set_roster(tmp_path, monkeypatch)
    yield state_env, tmp_path
    state._config_cache = None


def _alpha(data: dict) -> dict:
    return data["children"][0]


def test_fixture_has_the_keys_consumers_read():
    data = load("powerschool")
    assert isinstance(data["scraped_at"], str) and data["warnings"] == []
    for child in data["children"]:
        assert isinstance(child["name"], str) and child["name"].split()
        for key in ("missing", "late", "upcoming"):
            assert isinstance(child[key], list)
            for item in child[key]:
                assert set(ITEM_KEYS) <= set(item)


def test_fixture_is_accepted_end_to_end(env):
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path)
    s = built["state"]
    assert {k: (len(v["missing"]), len(v["late"]), len(v["upcoming"])) for k, v in s["powerschool"].items()} == {
        "Alpha": (1, 1, 1), "Beta": (0, 0, 1), "Gamma": (0, 0, 0)}
    assert s["powerschool_meta"] == {"warnings": []}

    out = consumers(built)
    assert "PS: missing 1 (Quiz 4 Corrections), late 1 (Essay Draft)" in out["quick"]
    ps_status = {i["title"]: i["status"] for i in out["items"] if i["source"] == "powerschool"}
    assert ps_status["Quiz 4 Corrections"] == "missing" and ps_status["Essay Draft"] == "late"
    block = powerschool.format_block(s, today=TODAY)
    assert "Quiz 4 Corrections" in block and "Essay Draft" in block and "Lab Report" in block


@pytest.mark.parametrize("mutation, reason", [
    pytest.param(lambda d: renamed(d, "children", "students"), "no children in scrape", id="children-renamed"),
    pytest.param(lambda d: {**d, "warnings": ["login failed"]}, "scraper reported warnings", id="top-level-warnings"),
    pytest.param(lambda d: {k: v for k, v in d.items() if k != "children"}, "no children in scrape", id="children-missing"),
])
def test_guarded_untrusted_scrape_records_a_warning(env, mutation, reason):
    state_env, tmp_path = env
    first = build_state(state_env, tmp_path)
    assert first["state"]["powerschool"]
    built = build_state(state_env, tmp_path, ps=mutation(load("powerschool")))
    assert built["state"]["powerschool"] == first["state"]["powerschool"]  # previous data kept
    assert reason in built["state"]["powerschool_meta"]["warnings"][0]


def test_guarded_renamed_list_keeps_previous_entry_with_a_warning(env):
    state_env, tmp_path = env
    bad = mutate(load("powerschool"), lambda d: d["children"].__setitem__(
        0, renamed(_alpha(d), "missing", "overdue")))
    built = build_state(state_env, tmp_path, ps=bad)
    assert "Alpha" not in built["state"]["powerschool"]
    assert built["state"]["powerschool_meta"]["warnings"] == ["PowerSchool Alpha: malformed lists, kept previous data"]
    assert len(built["state"]["powerschool"]) == 2


def test_child_name_key_renamed_is_flagged(env):
    """G12a (guarded): a child entry without `name` is skipped with a plain warning."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    bad = mutate(load("powerschool"), lambda d: d["children"].__setitem__(
        0, renamed(_alpha(d), "name", "student_name")))
    built = build_state(state_env, tmp_path, ps=bad)
    assert len(built["state"]["powerschool"]["Alpha"]["missing"]) == 1  # previous data kept
    assert built["state"]["powerschool_meta"]["warnings"] == [
        "PowerSchool: 1 child entry in the last scrape had no name and was skipped"]


def test_item_due_renamed_keeps_previous_data_with_a_warning(env):
    """G12b (guarded): `due` -> `due_date` on items no longer wipes due dates silently."""
    state_env, tmp_path = env
    first = build_state(state_env, tmp_path)
    previous = first["state"]["powerschool"]["Alpha"]["updated"][:10]

    def edit(d: dict) -> None:
        for child in d["children"]:
            for key in ("missing", "late", "upcoming"):
                child[key] = [renamed(i, "due", "due_date") for i in child[key]]

    built = build_state(state_env, tmp_path, ps=mutate(load("powerschool"), edit))
    assert built["state"]["powerschool_meta"]["warnings"] == [
        f"PowerSchool Alpha: items without a due field, kept previous data from {previous}",
        f"PowerSchool Beta: items without a due field, kept previous data from {previous}"]
    assert built["state"]["powerschool"] == first["state"]["powerschool"]
    assert "(due" in powerschool.format_block(built["state"], today=TODAY)


def test_item_title_missing_keeps_previous_data_with_a_warning(env):
    """G12c (guarded): items without `title` no longer render as 'Untitled' silently."""
    state_env, tmp_path = env
    first = build_state(state_env, tmp_path)
    previous = first["state"]["powerschool"]["Alpha"]["updated"][:10]

    def edit(d: dict) -> None:
        _alpha(d)["missing"] = [renamed(i, "title", "name") for i in _alpha(d)["missing"]]

    built = build_state(state_env, tmp_path, ps=mutate(load("powerschool"), edit))
    assert built["state"]["powerschool_meta"]["warnings"] == [
        f"PowerSchool Alpha: items without a title, kept previous data from {previous}"]
    block = powerschool.format_block(built["state"], today=TODAY)
    assert "Quiz 4 Corrections" in block and "Untitled" not in block


def test_item_fields_missing_without_previous_data_say_so(env):
    state_env, tmp_path = env
    bad = mutate(load("powerschool"), lambda d: _alpha(d).update(
        missing=[renamed(i, "title", "name") for i in _alpha(d)["missing"]]))
    built = build_state(state_env, tmp_path, ps=bad)
    assert "Alpha" not in built["state"]["powerschool"]
    assert built["state"]["powerschool_meta"]["warnings"] == [
        "PowerSchool Alpha: items without a title, no earlier data to show"]


def test_logging_stays_quiet_for_the_valid_fixture(env, caplog):
    state_env, tmp_path = env
    with caplog.at_level(logging.WARNING):
        build_state(state_env, tmp_path)
    assert not [r for r in caplog.records if "PowerSchool" in r.getMessage()]
