"""Contract: `sgy summary --json` output (tests/fixtures/contracts/sgy.json) vs school_dashboard.

Consumers and the keys they read:
  state.update_from_sgy_file       per_child[].child.name, .assignments[].{title, due_date},
                                   .grades[].{course, grade, letter}, .announcements
  overview_items._schoology_rows   assignments[].{title, course, due_date, status, link}
  readiness.get_checklist          assignments[].{title, course, due_date, status}
  digest.build_quick_check         assignments[].status
  schoology_autoarchive.scrape_view  top-level warnings, per_child[].child.name,
                                   .assignments[].title
"""
import ast
import json
from pathlib import Path
from datetime import datetime, timedelta

import pytest
from contract_support import (TODAY, assert_unguarded, build_state, consumers, line, load, mutate, reasons, renamed,
                              run_update, set_roster, warnings_of)

from school_dashboard import schoology_autoarchive, state


@pytest.fixture
def env(state_env, tmp_path, monkeypatch):
    set_roster(tmp_path, monkeypatch)
    yield state_env, tmp_path
    state._config_cache = None


def _alpha(data: dict) -> dict:
    return data["per_child"][0]


def _pending(built: dict) -> list:
    return [i for i in built["state"]["action_items"] if i["status"] == "pending"]


def test_fixture_has_the_keys_consumers_read():
    data = load("sgy")
    assert isinstance(data["per_child"], list) and len(data["per_child"]) == 3
    for entry in data["per_child"]:
        assert isinstance(entry["child"]["name"], str) and entry["child"]["name"].split()
        assert isinstance(entry["assignments"], list)
        assert isinstance(entry["grades"], list) and isinstance(entry["announcements"], list)
        for a in entry["assignments"]:
            for key in ("title", "course", "due_date", "status", "link"):
                assert isinstance(a[key], str), key
        for g in entry["grades"]:
            for key in ("course", "grade", "letter"):
                assert isinstance(g[key], str), key


def test_fixture_is_accepted_end_to_end(env):
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path)
    sgy = built["state"]["schoology"]
    assert {k: len(v["assignments"]) for k, v in sgy.items()} == {"Alpha": 5, "Beta": 1}
    assert sgy["Alpha"]["grades"] == [
        {"course": "Sample Math", "grade": "92%", "letter": "A"},
        {"course": "Sample ELA", "grade": "78%", "letter": "C"}]
    assert len(sgy["Alpha"]["announcements"]) == 1
    # 5 + 1 assignments become action items; the undated one has due=None
    pending = _pending(built)
    assert len(pending) == 6
    assert [i["due"] for i in pending if i["summary"] == "Undated Project"] == [None]

    out = consumers(built)
    assert "Alpha: IXL 4 remaining (Math, ELA), SGY 4 open" in out["quick"]
    by_title = {i["title"]: i["status"] for i in out["items"] if i["source"] == "schoology"}
    assert by_title == {"Synthetic Worksheet 1": "tonight", "Old Quiz Corrections": "overdue",
                        "Practice Set A": "upcoming"}
    labels = {(i["label"], i["urgency"]) for i in out["readiness"]["Alpha"] if i["type"] == "assignment"}
    assert labels == {("Synthetic Worksheet 1", "tomorrow"), ("Old Quiz Corrections", "overdue")}
    assert "Beta" not in out["readiness"] or not [
        i for i in out["readiness"]["Beta"] if i["type"] == "assignment"]


def test_autoarchive_reads_the_fixture(tmp_path):
    path = tmp_path / "sgy.json"
    path.write_text(json.dumps(load("sgy")))
    seen, trusted, digest = schoology_autoarchive.scrape_view(str(path))
    assert {k: len(v) for k, v in seen.items()} == {"Alpha": 5, "Beta": 1, "Gamma": 0}
    assert digest


def test_failed_and_partial_scrapes_are_untrusted_for_autoarchive(tmp_path):
    """G9 (guarded): `sgy summary` has no top-level `warnings` key, so trust comes from the
    per-child `scrape_confidence`: partial (Beta) and failed (Gamma) are not trusted."""
    path = tmp_path / "sgy.json"
    path.write_text(json.dumps(load("sgy")))
    _, trusted, _ = schoology_autoarchive.scrape_view(str(path))
    assert "warnings" not in load("sgy")
    assert trusted == {"Alpha": True, "Beta": False, "Gamma": False}


def test_failed_child_counts_no_misses_and_archives_nothing(tmp_path):
    now = datetime(2026, 11, 5, 12, 0, 0)
    old = (now - timedelta(days=10)).isoformat()
    item = {"id": "x1", "child": "Gamma", "source": "schoology", "type": "assignment",
            "summary": "Old", "due": None, "status": "pending", "created": old,
            "last_seen": old, "missed": 2}
    st = {"action_items": [item]}
    path = tmp_path / "sgy.json"
    path.write_text(json.dumps(load("sgy")))
    seen, trusted, _ = schoology_autoarchive.scrape_view(str(path))
    new_state, archived, _ = schoology_autoarchive.plan_autoarchive(st, seen, trusted, now)
    assert archived == []
    assert new_state["action_items"][0]["missed"] == 2


NONAME_WARNING = ("Schoology: a child in the last scrape had no name and was skipped; "
                  "check that the Schoology scraper still reports child names")


def _sgy_warnings(built: dict) -> list:
    return warnings_of(built, "Schoology")


def test_failed_child_is_unavailable_not_all_done(env):
    """G8 (guarded): a failed child (scrape_confidence 'failed') says Schoology unavailable."""
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path)
    assert load("sgy")["per_child"][2]["scrape_confidence"] == "failed"
    assert "Gamma" not in built["state"]["schoology"]
    assert reasons(built, "schoology") == {"Gamma": "failed"}
    assert _sgy_warnings(built) == ["Schoology Gamma: the last scrape failed; no earlier data to show"]
    assert line(consumers(built)["quick"], "Gamma") == "Gamma: IXL all done, Schoology unavailable"


def test_partial_child_is_merged_and_not_warned_about(env):
    """M2: partial children keep their data in state, show no warning, but are never trusted
    for auto-archive (see test_failed_and_partial_scrapes_are_untrusted_for_autoarchive)."""
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path)
    assert len(built["state"]["schoology"]["Beta"]["assignments"]) == 1
    assert "Beta" not in reasons(built, "schoology")
    assert line(consumers(built)["quick"], "Beta") == "Beta: IXL all done, SGY all done"


def test_failed_child_keeps_previous_items(env):
    state_env, tmp_path = env
    first = mutate(load("sgy"), lambda d: d["per_child"][2].update(
        scrape_confidence="high", assignments=[{
            "title": "Gamma Task", "course": "Sample", "due_date": "", "status": "unknown", "link": ""}]))
    build_state(state_env, tmp_path, sgy=first)
    built = build_state(state_env, tmp_path)
    assert [a["title"] for a in built["state"]["schoology"]["Gamma"]["assignments"]] == ["Gamma Task"]
    assert _sgy_warnings(built) == [
        "Schoology Gamma: the last scrape failed; showing the last good data from 2026-11-05"]
    assert "Gamma Task" in {i["summary"] for i in _pending(built)}
    assert line(consumers(built)["quick"], "Gamma") == "Gamma: IXL all done, Schoology unavailable"


def test_per_child_renamed_is_flagged_and_keeps_previous_data(env):
    """G4 (guarded): `per_child` renamed -> a warning, previous schoology section kept."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    built = build_state(state_env, tmp_path, sgy=renamed(load("sgy"), "per_child", "children_data"))
    assert set(built["state"]["schoology"]) == {"Alpha", "Beta"}
    assert len(_pending(built)) == 6
    assert reasons(built, "schoology") == {"*": "unexpected"}
    assert _sgy_warnings(built) == [
        "Schoology: the last scrape returned unexpected data; showing the last good data from 2026-11-05"]
    assert line(consumers(built)["quick"], "Alpha").endswith("Schoology unavailable")


def test_per_child_renamed_without_previous_data_is_flagged(env):
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, sgy=renamed(load("sgy"), "per_child", "children_data"))
    assert built["state"]["schoology"] == {}
    assert _sgy_warnings(built) == ["Schoology: the last scrape returned unexpected data; no earlier data to show"]
    assert line(consumers(built)["quick"], "Alpha") == "Alpha: IXL 4 remaining (Math, ELA), Schoology unavailable"


def test_empty_per_child_after_data_is_suspicious(env):
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    built = build_state(state_env, tmp_path, sgy={**load("sgy"), "per_child": []})
    assert set(built["state"]["schoology"]) == {"Alpha", "Beta"}
    assert reasons(built, "schoology") == {"*": "empty"}


def test_empty_per_child_with_nothing_before_is_not_flagged(env):
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, sgy={**load("sgy"), "per_child": []})
    assert reasons(built, "schoology") == {}


@pytest.mark.parametrize("content, expected", [
    pytest.param(None, "missing", id="file-missing"),
    pytest.param("{not json", "unexpected", id="invalid-json"),
    pytest.param("[]", "unexpected", id="not-an-object"),
])
def test_unusable_sgy_file_is_a_whole_source_problem_and_recovers(env, content, expected):
    """M3: early-return paths record `*`; the next clean scrape clears it."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    sgy_file = tmp_path / "inputs" / "sgy.json"
    if content is None:
        sgy_file.unlink()
    else:
        sgy_file.write_text(content)
    built = run_update(state_env, tmp_path)
    assert set(built["state"]["schoology"]) == {"Alpha", "Beta"}
    assert reasons(built, "schoology") == {"*": expected}
    assert line(consumers(built)["quick"], "Alpha").endswith("Schoology unavailable")
    sgy_file.write_text(json.dumps(load("sgy")))
    assert reasons(run_update(state_env, tmp_path), "schoology") == {"Gamma": "failed"}


def test_unavailable_names_only_include_roster_children(env):
    """L1: a stray file for a child outside the roster does not invent a quick-check line."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    (tmp_path / "inputs" / "ixl" / "zed-assigned.json").write_text("{}")
    built = run_update(state_env, tmp_path)
    assert reasons(built, "ixl") == {"Zed": "unexpected"}
    assert not [ln for ln in consumers(built)["quick"].splitlines() if ln.startswith("Zed")]


def test_bad_scrape_reaches_api_overview_warnings(env, monkeypatch):
    """End to end: bad scrape file -> `school-state update` -> /api/overview warning text."""
    from web.app import app as flask_app
    state_env, tmp_path = env
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(state_env / "school-state.json"))
    build_state(state_env, tmp_path, sgy=renamed(load("sgy"), "per_child", "children_data"))
    with flask_app.test_client() as c:
        body = c.get("/api/overview").get_json()
    assert "Schoology: the last scrape returned unexpected data; no earlier data to show" in body["warnings"]


def test_child_name_key_missing_is_flagged_not_a_crash(env):
    """G5 (guarded): a child entry without a name no longer raises a bare IndexError; that child
    is skipped (previous data kept), the others still merge, and a plain warning is recorded."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(child=renamed(_alpha(d)["child"], "name", "full_name")))
    built = build_state(state_env, tmp_path, sgy=bad)
    assert set(built["state"]["schoology"]) == {"Alpha", "Beta"}
    assert len(built["state"]["schoology"]["Alpha"]["assignments"]) == 5
    assert reasons(built, "schoology") == {"?": "noname", "Gamma": "failed"}
    assert _sgy_warnings(built) == [
        NONAME_WARNING,
        "Schoology Gamma: the last scrape failed; no earlier data to show"]
    assert "Schoology unavailable" not in line(consumers(built)["quick"], "Alpha")


def test_non_dict_child_entry_is_flagged(env):
    state_env, tmp_path = env
    bad = mutate(load("sgy"), lambda d: d["per_child"].append("oops"))
    built = build_state(state_env, tmp_path, sgy=bad)
    assert reasons(built, "schoology") == {"Gamma": "failed", "?": "noname"}


def _alpha_previous_kept(built: dict) -> None:
    assert len(built["state"]["schoology"]["Alpha"]["assignments"]) == 5
    assert reasons(built, "schoology") == {"Alpha": "unexpected", "Gamma": "failed"}
    assert ("Schoology Alpha: the last scrape returned unexpected data; "
            "showing the last good data from 2026-11-05") in _sgy_warnings(built)
    assert line(consumers(built)["quick"], "Alpha").endswith("Schoology unavailable")


def test_due_date_renamed_is_flagged_and_keeps_previous_data(env):
    """G6 (guarded): `due_date` -> `dueDate` no longer drops every dated row silently."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(
        assignments=[renamed(a, "due_date", "dueDate") for a in _alpha(d)["assignments"]]))
    built = build_state(state_env, tmp_path, sgy=bad)
    _alpha_previous_kept(built)
    assert any(i["due"] for i in _pending(built) if i["child"] == "Alpha")
    assert [i for i in consumers(built)["items"] if i["source"] == "schoology"]


def test_title_renamed_is_flagged_and_keeps_previous_items(env):
    """G7 (guarded): `title` renamed no longer collapses Alpha's items into one 'Unknown'."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(
        assignments=[renamed(a, "title", "name") for a in _alpha(d)["assignments"]]))
    built = build_state(state_env, tmp_path, sgy=bad)
    _alpha_previous_kept(built)
    alpha_items = [i["summary"] for i in _pending(built) if i["child"] == "Alpha"]
    assert len(alpha_items) == 5 and "Unknown" not in alpha_items


def test_renamed_fields_without_previous_data_are_flagged(env):
    state_env, tmp_path = env
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(
        assignments=[renamed(a, "title", "name") for a in _alpha(d)["assignments"]]))
    built = build_state(state_env, tmp_path, sgy=bad)
    assert "Alpha" not in built["state"]["schoology"]
    assert [i for i in _pending(built) if i["child"] == "Alpha"] == []
    assert ("Schoology Alpha: the last scrape returned unexpected data; "
            "no earlier data to show") in _sgy_warnings(built)


def test_renamed_assignment_fields_are_untrusted_for_autoarchive(tmp_path):
    """G7: a collapsed 'Unknown' id set must not count misses against the real items."""
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(
        assignments=[renamed(a, "title", "name") for a in _alpha(d)["assignments"]]))
    path = tmp_path / "sgy.json"
    path.write_text(json.dumps(bad))
    _, trusted, _ = schoology_autoarchive.scrape_view(str(path))
    assert trusted["Alpha"] is False


def test_inconsistency_status_case_readiness_vs_overview(env):
    """The scraper emits the page's status text unlowercased ('Submitted'). Overview and the
    quick check lowercase it; readiness compares case-sensitively and still lists the item."""
    state_env, tmp_path = env
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    bad = mutate(load("sgy"), lambda d: _alpha(d)["assignments"][1].update(due_date=tomorrow))
    out = consumers(build_state(state_env, tmp_path, sgy=bad))
    assert "Sample Reading Log" not in {i["title"] for i in out["items"]}
    assert_unguarded("Sample Reading Log" in {i["label"] for i in out["readiness"]["Alpha"]}, "G10")


def test_inconsistency_freeform_due_date_readiness_vs_overview(env):
    """'Month D, YYYY' due dates (unparsed page text) are understood by overview but silently
    skipped by readiness (date.fromisoformat)."""
    state_env, tmp_path = env
    tomorrow = TODAY + timedelta(days=1)
    freeform = f"{tomorrow:%B} {tomorrow.day}, {tomorrow.year}"
    bad = mutate(load("sgy"), lambda d: _alpha(d)["assignments"][2].update(due_date=freeform))
    out = consumers(build_state(state_env, tmp_path, sgy=bad))
    assert "Practice Set A" in {i["title"] for i in out["items"]}
    assert_unguarded("Practice Set A" not in {i["label"] for i in out["readiness"]["Alpha"]}, "G11")


def test_unused_keys_can_vanish_without_effect(env):
    """Keys the dashboard never reads (homework_slides, scrape_stages, building, link) may be
    dropped without any change to the merged state."""
    state_env, tmp_path = env
    base = build_state(state_env, tmp_path)["state"]["schoology"]
    def drop(d: dict) -> None:
        for entry in d["per_child"]:
            del entry["homework_slides"], entry["scrape_stages"]

    slim = mutate(load("sgy"), drop)
    again = build_state(state_env, tmp_path, sgy=slim)["state"]["schoology"]
    assert {k: len(v["assignments"]) for k, v in again.items()} == {
        k: len(v["assignments"]) for k, v in base.items()}


def test_silent_degradation_readiness_omits_children_without_schoology(env):
    """UNGUARDED: readiness iterates only state['schoology']; Alpha has IXL data but no
    schoology entry here, so it gets no checklist at all."""
    state_env, tmp_path = env
    bad = mutate(load("sgy"), lambda d: d["per_child"].pop(0))
    built = build_state(state_env, tmp_path, sgy=bad)
    assert built["state"]["ixl"]["Alpha"]["totals"]
    assert_unguarded("Alpha" not in consumers(built)["readiness"], "G15")


def test_missing_due_date_alone_makes_a_child_untrusted_for_autoarchive(tmp_path):
    """The auto-archive and the state merge share one predicate (state.sgy_assignments_problem)."""
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(
        assignments=[renamed(a, "due_date", "dueDate") for a in _alpha(d)["assignments"]]))
    path = tmp_path / "sgy.json"
    path.write_text(json.dumps(bad))
    _, trusted, _ = schoology_autoarchive.scrape_view(str(path))
    assert trusted["Alpha"] is False and trusted["Beta"] is False  # Beta is partial, still untrusted


def test_unnamed_marker_counts_towards_the_watchdog_age(env):
    from school_dashboard import scrape_meta
    state_env, tmp_path = env
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(child={}))
    built = build_state(state_env, tmp_path, sgy=bad)
    assert scrape_meta.oldest_since(built["state"], "schoology") is not None
    only_unnamed = {"scrape_meta": {"schoology": {"unavailable": {
        "?": {"reason": "noname", "since": "2026-11-01T00:00:00+00:00"}}}}}
    assert scrape_meta.oldest_since(only_unnamed, "schoology").day == 1
    assert scrape_meta.unavailable_names(only_unnamed) == []


VENDOR_SGY = Path(__file__).resolve().parent.parent / "vendor" / "schoology-scrape" / "sgy_cli" / "cli.py"


@pytest.mark.skipif(not VENDOR_SGY.is_file(), reason="schoology-scrape is not vendored here")
def test_every_vendored_assignment_builder_emits_title_and_due_date():
    """The guard in sgy_assignments_problem is only safe if no scraper path omits these keys."""
    tree = ast.parse(VENDOR_SGY.read_text())
    builders = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)
                and {"title", "status", "link"} <= {k.value for k in n.keys if isinstance(k, ast.Constant)}]
    assert len(builders) >= 5
    for node in builders:
        keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
        assert {"title", "due_date"} <= keys, f"line {node.lineno}"
