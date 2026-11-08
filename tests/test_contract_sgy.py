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
import json
from datetime import datetime, timedelta

import pytest
from contract_support import (TODAY, assert_unguarded, build_state, consumers, load, mutate, renamed, set_roster,
                              unguarded_raises)

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


def _sgy_meta(built: dict) -> dict:
    return built["state"]["scrape_meta"]["schoology"]


def test_failed_child_is_unavailable_not_all_done(env):
    """G8 (guarded): a failed child (scrape_confidence 'failed') says Schoology unavailable."""
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path)
    assert load("sgy")["per_child"][2]["scrape_confidence"] == "failed"
    assert "Gamma" not in built["state"]["schoology"]
    assert _sgy_meta(built) == {"unavailable": {"Gamma": "scrape failed"},
                                "warnings": ["Schoology Gamma: scrape failed; no previous data"]}
    quick = consumers(built)["quick"]
    assert "Gamma: IXL all done, Schoology unavailable" in quick
    assert "Gamma: IXL all done, SGY all done" not in quick


def test_failed_child_keeps_previous_items_and_partial_child_is_merged(env):
    state_env, tmp_path = env
    first = mutate(load("sgy"), lambda d: d["per_child"][2].update(
        scrape_confidence="high", assignments=[{
            "title": "Gamma Task", "course": "Sample", "due_date": "", "status": "unknown", "link": ""}]))
    build_state(state_env, tmp_path, sgy=first)
    built = build_state(state_env, tmp_path)
    assert [a["title"] for a in built["state"]["schoology"]["Gamma"]["assignments"]] == ["Gamma Task"]
    assert _sgy_meta(built)["warnings"] == ["Schoology Gamma: scrape failed; previous data kept"]
    assert len(built["state"]["schoology"]["Beta"]["assignments"]) == 1
    assert "Gamma Task" in {i["summary"] for i in _pending(built)}
    assert "Gamma: IXL all done, Schoology unavailable" in consumers(built)["quick"]


def test_per_child_renamed_is_flagged_and_keeps_previous_data(env):
    """G4 (guarded): `per_child` renamed -> a warning, previous schoology section kept."""
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    built = build_state(state_env, tmp_path, sgy=renamed(load("sgy"), "per_child", "children_data"))
    assert set(built["state"]["schoology"]) == {"Alpha", "Beta"}
    assert len(_pending(built)) == 6
    assert _sgy_meta(built) == {"unavailable": {"*": "per_child missing"},
                                "warnings": ["Schoology: per_child missing; previous data kept"]}
    assert "Schoology unavailable" in consumers(built)["quick"]


def test_per_child_renamed_without_previous_data_is_flagged(env):
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, sgy=renamed(load("sgy"), "per_child", "children_data"))
    assert built["state"]["schoology"] == {}
    assert _sgy_meta(built)["warnings"] == ["Schoology: per_child missing; no previous data"]
    assert "Alpha: IXL 4 remaining (Math, ELA), Schoology unavailable" in consumers(built)["quick"]


def test_empty_per_child_after_data_is_suspicious(env):
    state_env, tmp_path = env
    build_state(state_env, tmp_path)
    built = build_state(state_env, tmp_path, sgy={**load("sgy"), "per_child": []})
    assert set(built["state"]["schoology"]) == {"Alpha", "Beta"}
    assert _sgy_meta(built)["warnings"] == ["Schoology: scrape returned no children; previous data kept"]


def test_empty_per_child_with_nothing_before_is_not_flagged(env):
    state_env, tmp_path = env
    built = build_state(state_env, tmp_path, sgy={**load("sgy"), "per_child": []})
    assert _sgy_meta(built) == {"unavailable": {}, "warnings": []}


def test_child_name_key_missing_crashes_the_update(env):
    """Fails loudly, but as a bare IndexError from `''.split()[0]` in update_from_sgy_file."""
    state_env, tmp_path = env
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(child=renamed(_alpha(d)["child"], "name", "full_name")))
    with unguarded_raises(IndexError, "G5"):
        build_state(state_env, tmp_path, sgy=bad)


def test_silent_degradation_due_date_renamed_drops_all_dates(env):
    """UNGUARDED (the roadmap's example): `due_date` -> `dueDate`. Items still count as
    open, but overview drops every row (no date) and readiness lists nothing."""
    state_env, tmp_path = env
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(
        assignments=[renamed(a, "due_date", "dueDate") for a in _alpha(d)["assignments"]]))
    built = build_state(state_env, tmp_path, sgy=bad)
    out = consumers(built)
    assert_unguarded("SGY 4 open" in out["quick"], "G6")
    assert_unguarded(not [i for i in out["items"] if i["source"] == "schoology"], "G6")
    assert_unguarded(not [i for i in out["readiness"].get("Alpha", []) if i["type"] == "assignment"], "G6")
    assert_unguarded(all(i["due"] is None for i in _pending(built) if i["child"] == "Alpha"), "G6")


def test_silent_degradation_title_renamed_collapses_action_items(env):
    """UNGUARDED: `title` renamed -> every assignment is 'Unknown' and the action-id
    de-duplication merges Alpha's five assignments into one pending item."""
    state_env, tmp_path = env
    bad = mutate(load("sgy"), lambda d: _alpha(d).update(
        assignments=[renamed(a, "title", "name") for a in _alpha(d)["assignments"]]))
    built = build_state(state_env, tmp_path, sgy=bad)
    alpha_items = [i for i in _pending(built) if i["child"] == "Alpha"]
    assert_unguarded([i["summary"] for i in alpha_items] == ["Unknown"], "G7")


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
