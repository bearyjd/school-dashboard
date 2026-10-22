"""`school-state schoology reset`: archive stale Schoology data, rebuild from one scrape."""
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from school_dashboard import maintenance, schoology_reset, state

REPO = Path(__file__).resolve().parent.parent


def _item(child, summary, source="schoology", status="pending", due=None, created="2025-09-01T08:00:00"):
    return {
        "id": state._action_id(source, summary, child),
        "child": child, "source": source, "type": "assignment", "summary": summary,
        "due": due, "status": status, "created": created,
    }


def _sgy(*children):
    """children: (full name, [(title, due_date)])"""
    return {"per_child": [
        {"child": {"name": name},
         "assignments": [{"title": t, "due_date": d} for t, d in items],
         "grades": [{"course": "Math", "grade": "90", "letter": "A", "extra": 1}],
         "announcements": [{"title": "hi"}]}
        for name, items in children
    ]}


@pytest.fixture
def env(state_env, tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"children": {}, "name_aliases": {"pennington": "Penn"}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.setattr(state, "_config_cache", None)
    maintenance.enable("test")
    return state_env


def _write_state(env, **over):
    base = {
        "last_updated": "2026-01-01T00:00:00",
        "children": {"Jack": {"grade": "5", "school": "X"}},
        "ixl": {"Jack": {"totals": {}}},
        "schoology": {"John": {"assignments": []}, "Pennington": {"assignments": []},
                      "Jack": {"updated": "old", "assignments": [], "grades": [], "announcements": []}},
        "action_items": [],
        "calendar_events_created": ["e1"],
        "email_last_scan": "2026-01-01",
    }
    base.update(over)
    p = env / "school-state.json"
    p.write_text(json.dumps(base, indent=2))
    return p


def _write_sgy(env, data, name="sgy.json"):
    p = env / name
    p.write_text(json.dumps(data))
    return p


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _run(env, sgy, **kw):
    return schoology_reset.run_reset(sgy, kw.pop("archive_dir", None), kw.pop("max_age_hours", 24), kw.pop("dry_run", False), **kw)


def _pending_sgy(s):
    return [i for i in s["action_items"] if i["source"] == "schoology" and i["status"] == "pending"]


# ---- preconditions ---------------------------------------------------------

def test_inactive_maintenance_refuses(env):
    maintenance.disable()
    sp = _write_state(env)
    before = _sha(sp)
    sgy = _write_sgy(env, _sgy(("John Smith", [("HW1", "2026-10-30")])))
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 3
    assert "school-state transition maintenance on" in str(ei.value)
    assert _sha(sp) == before and not (env / "archive").exists()


def _bad_sgy_cases():
    return {
        "missing": None,
        "garbage": "{not json",
        "not_object": "[1]",
        "no_per_child": json.dumps({"x": 1}),
        "empty_children": json.dumps({"per_child": []}),
        "nameless_children": json.dumps({"per_child": [{"child": {"name": " "}}]}),
        "bad_assignments": json.dumps({"per_child": [{"child": {"name": "A B"}, "assignments": "x"}]}),
    }


@pytest.mark.parametrize("case", list(_bad_sgy_cases()))
def test_bad_sgy_file_writes_nothing(env, case):
    sp = _write_state(env, action_items=[_item("Jack", "old")])
    before = _sha(sp)
    raw = _bad_sgy_cases()[case]
    sgy = env / "sgy.json"
    if raw is not None:
        sgy.write_text(raw)
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 2
    assert _sha(sp) == before and not (env / "archive").exists()


def test_stale_sgy_file_refused(env):
    sp = _write_state(env)
    before = _sha(sp)
    sgy = _write_sgy(env, _sgy(("John S", [("HW", None)])))
    old = time.time() - 48 * 3600
    os.utime(sgy, (old, old))
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 2 and "old" in str(ei.value)
    assert _sha(sp) == before and not (env / "archive").exists()


def test_stale_ok_with_larger_max_age(env):
    _write_state(env)
    sgy = _write_sgy(env, _sgy(("John S", [("HW", None)])))
    old = time.time() - 48 * 3600
    os.utime(sgy, (old, old))
    assert _run(env, sgy, max_age_hours=72)["changed"] is True


@pytest.mark.parametrize("bad", ["notjson", None, {"action_items": {}}, {"action_items": "x"}, []])
def test_bad_state_refused(env, bad):
    sp = env / "school-state.json"
    if bad is None:
        pass  # missing file
    elif isinstance(bad, str):
        sp.write_text(bad)
    elif isinstance(bad, list):
        sp.write_text(json.dumps(bad))
    else:
        sp.write_text(json.dumps({**bad, "schoology": {}}))
    sgy = _write_sgy(env, _sgy(("John S", [("HW", None)])))
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 2
    assert not (env / "archive").exists()


# ---- mapping / canonical names --------------------------------------------

def test_canonical_names_and_section_rebuilt(env):
    sp = _write_state(env)
    sgy = _write_sgy(env, _sgy(("John Smith", [("HW1", "2026-10-30")]),
                              ("Pennington Smith", [("Read", None)])))
    rep = _run(env, sgy)
    s = json.loads(sp.read_text())
    assert set(s["schoology"]) == {"Jack", "Penn"}
    assert s["schoology"]["Jack"]["grades"] == [{"course": "Math", "grade": "90", "letter": "A"}]
    assert {i["child"] for i in _pending_sgy(s)} == {"Jack", "Penn"}
    assert sorted(rep["dropped_alias_keys"]) == ["John", "Pennington"]
    assert sorted(rep["new_schoology_children"]) == ["Jack", "Penn"]
    ids = {i["id"] for i in _pending_sgy(s)}
    assert ids == {state._action_id("schoology", "HW1", "Jack"), state._action_id("schoology", "Read", "Penn")}
    other = next(i for i in s["action_items"] if i["summary"] == "Read")
    assert other["due"] is None and other["status"] == "pending" and other["type"] == "assignment"


# ---- archive ---------------------------------------------------------------

def test_archive_contents_and_perms(env):
    old = _item("Jack", "Gone")
    sp = _write_state(env, action_items=[old])
    os.chmod(sp, 0o640)
    sgy = _write_sgy(env, _sgy(("Jack S", [("New", None)])))
    rep = _run(env, sgy)
    arch = Path(rep["archive_path"])
    assert arch.parent == env / "archive" and arch.name.startswith("schoology-")
    assert stat.S_IMODE(arch.stat().st_mode) == 0o600
    assert stat.S_IMODE(arch.parent.stat().st_mode) == 0o700
    data = json.loads(arch.read_text())
    assert data["items"] == [old]
    assert set(data["schoology_section"]) == {"John", "Pennington", "Jack"}
    assert data["counts"]["items"] == 1
    assert data["sgy_file"] == str(sgy) and data["state_file"] == str(sp)
    assert "created_at" in data
    assert stat.S_IMODE(sp.stat().st_mode) == 0o640  # existing mode kept


def test_custom_archive_dir(env, tmp_path):
    _write_state(env, action_items=[_item("Jack", "Gone")])
    sgy = _write_sgy(env, _sgy(("Jack S", [("New", None)])))
    rep = _run(env, sgy, archive_dir=tmp_path / "elsewhere")
    assert Path(rep["archive_path"]).parent == tmp_path / "elsewhere"


def test_state_write_failure_leaves_state_unchanged(env, monkeypatch):
    sp = _write_state(env, action_items=[_item("Jack", "Gone")])
    before = _sha(sp)
    sgy = _write_sgy(env, _sgy(("Jack S", [("New", None)])))
    real = schoology_reset.write_atomic

    def boom(path, data, mode=0o600):
        if Path(path) == sp:
            raise OSError("disk full")
        return real(path, data, mode)

    monkeypatch.setattr(schoology_reset, "write_atomic", boom)
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 1 and "unchanged" in str(ei.value)
    assert _sha(sp) == before
    assert len(list((env / "archive").glob("schoology-*.json"))) == 1


def test_archive_verification_failure_blocks_state_swap(env, monkeypatch):
    sp = _write_state(env, action_items=[_item("Jack", "Gone")])
    before = _sha(sp)
    sgy = _write_sgy(env, _sgy(("Jack S", [("New", None)])))
    real = schoology_reset.write_atomic

    def corrupt(path, data, mode=0o600):
        if Path(path).parent.name == "archive":
            data = "{truncated"
        return real(path, data, mode)

    monkeypatch.setattr(schoology_reset, "write_atomic", corrupt)
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 1
    assert _sha(sp) == before


# ---- preservation / staleness ---------------------------------------------

def test_preserves_non_schoology_and_completed(env):
    items = [
        _item("Jack", "email thing", source="email"),
        _item("Jack", "manual thing", source="manual"),
        _item("Jack", "other", source="gc"),
        _item("Jack", "done last year", status="completed"),
        _item("Jack", "stale", due="Due Fri"),
    ]
    sp = _write_state(env, action_items=items)
    sgy = _write_sgy(env, _sgy(("Jack S", [("fresh", None)])))
    _run(env, sgy)
    s = json.loads(sp.read_text())
    summaries = {i["summary"] for i in s["action_items"]}
    assert summaries == {"email thing", "manual thing", "other", "done last year", "fresh"}
    for k in ("ixl", "children", "email_last_scan", "calendar_events_created"):
        assert s[k] == json.loads(json.dumps(_base_value(k)))


def _base_value(k):
    return {"ixl": {"Jack": {"totals": {}}}, "children": {"Jack": {"grade": "5", "school": "X"}},
            "email_last_scan": "2026-01-01", "calendar_events_created": ["e1"]}[k]


def test_completed_id_in_scrape_is_not_readded(env):
    done = _item("Jack", "HW", status="completed")
    sp = _write_state(env, action_items=[done])
    sgy = _write_sgy(env, _sgy(("Jack S", [("HW", None)])))
    _run(env, sgy)
    s = json.loads(sp.read_text())
    assert s["action_items"] == [done]


def test_stale_archived_current_keeps_created(env):
    cur = _item("Jack", "Current", due=None, created="2026-09-01T00:00:00")
    cur2 = _item("Jack", "CurrentDue", due="2026-11-01", created="2026-09-02T00:00:00")
    stale_undated = _item("Jack", "Last year undated")
    stale_fri = _item("Jack", "Last year", due="Due Fri")
    sp = _write_state(env, action_items=[stale_undated, cur, stale_fri, cur2])
    sgy = _write_sgy(env, _sgy(("Jack S", [("Current", "2026-10-31"), ("CurrentDue", None), ("Brand new", None)])))
    rep = _run(env, sgy)
    s = json.loads(sp.read_text())
    by = {i["summary"]: i for i in s["action_items"]}
    assert set(by) == {"Current", "CurrentDue", "Brand new"}
    assert by["Current"]["created"] == "2026-09-01T00:00:00"
    assert by["Current"]["due"] == "2026-10-31"  # new scrape supplies the missing due
    assert by["CurrentDue"]["due"] == "2026-11-01"  # old due kept
    assert by["CurrentDue"]["created"] == "2026-09-02T00:00:00"
    assert by["Brand new"]["created"] != "2025-09-01T08:00:00"
    assert rep["archived_items"] == 2
    arch = json.loads(Path(rep["archive_path"]).read_text())
    assert {i["summary"] for i in arch["items"]} == {"Last year undated", "Last year"}
    assert rep["old_pending_by_child"] == {"Jack": 4}
    assert rep["new_pending_by_child"] == {"Jack": 3}
    assert rep["items_kept"] == 2 and rep["items_added"] == 1


def test_duplicate_titles_in_scrape_collapse(env):
    sp = _write_state(env)
    sgy = _write_sgy(env, _sgy(("Jack S", [("Same", None), ("Same", "2026-11-02")])))
    _run(env, sgy)
    s = json.loads(sp.read_text())
    items = _pending_sgy(s)
    assert len(items) == 1 and items[0]["due"] == "2026-11-02"


# ---- idempotency / dry-run -------------------------------------------------

def test_idempotent(env):
    sp = _write_state(env, action_items=[_item("Jack", "Gone")])
    sgy = _write_sgy(env, _sgy(("John S", [("HW", "2026-11-01")]), ("Pennington S", [("R", None)])))
    first = _run(env, sgy)
    assert first["changed"] is True
    after = _sha(sp)
    n = len(list((env / "archive").glob("*")))
    second = _run(env, sgy)
    assert second["changed"] is False and second["archive_path"] is None
    assert _sha(sp) == after
    assert len(list((env / "archive").glob("*"))) == n


def test_dry_run_is_read_only(env):
    sp = _write_state(env, action_items=[_item("Jack", "Gone")])
    before = _sha(sp)
    mtime = sp.stat().st_mtime_ns
    sgy = _write_sgy(env, _sgy(("John S", [("HW", None)])))
    rep = _run(env, sgy, dry_run=True)
    assert rep["dry_run"] is True and rep["archived_items"] == 1 and rep["archive_path"] is None
    assert _sha(sp) == before and sp.stat().st_mtime_ns == mtime
    assert not (env / "archive").exists()
    assert [p.name for p in env.iterdir() if p.name.startswith(".")] == []


def test_dry_run_works_with_maintenance_inactive(env):
    maintenance.disable()
    _write_state(env)
    sgy = _write_sgy(env, _sgy(("John S", [("HW", None)])))
    assert _run(env, sgy, dry_run=True)["changed"] is True


def test_render_text_wording(env):
    _write_state(env, action_items=[_item("Jack", "Gone")])
    sgy = _write_sgy(env, _sgy(("John S", [("HW", None)])))
    dry = schoology_reset.render_text(_run(env, sgy, dry_run=True))
    assert "would" in dry.lower()
    real = schoology_reset.render_text(_run(env, sgy))
    assert "would" not in real.lower() and "archive" in real.lower()
    assert "nothing to do" in schoology_reset.render_text(_run(env, sgy)).lower()


# ---- CLI end to end --------------------------------------------------------

def _cli(env, tmp_path, *args):
    e = {**os.environ, "SCHOOL_STATE_DIR": str(env), "PYTHONPATH": str(REPO),
         "SCHOOL_DASHBOARD_CONFIG": str(tmp_path / "config.json")}
    e.pop("SCHOOL_STATE_PATH", None)
    return subprocess.run([sys.executable, "-m", "school_dashboard.cli", "schoology", "reset", *args],
                          capture_output=True, text=True, env=e, cwd=REPO)


def test_cli_end_to_end(env, tmp_path):
    sp = _write_state(env, action_items=[_item("Jack", "Gone")])
    sgy = _write_sgy(env, _sgy(("John S", [("HW", None)])))
    dry = _cli(env, tmp_path, "--sgy-file", str(sgy), "--dry-run", "--json")
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["dry_run"] is True
    real = _cli(env, tmp_path, "--sgy-file", str(sgy), "--json")
    assert real.returncode == 0, real.stderr
    rep = json.loads(real.stdout)
    assert rep["archived_items"] == 1 and Path(rep["archive_path"]).exists()
    assert {i["summary"] for i in json.loads(sp.read_text())["action_items"]} == {"HW"}
    again = _cli(env, tmp_path, "--sgy-file", str(sgy))
    assert again.returncode == 0 and "nothing to do" in again.stdout.lower()


def test_cli_exit_codes(env, tmp_path):
    _write_state(env)
    sgy = _write_sgy(env, {"per_child": []})
    assert _cli(env, tmp_path, "--sgy-file", str(sgy)).returncode == 2
    maintenance.disable()
    good = _write_sgy(env, _sgy(("John S", [("HW", None)])), "good.json")
    r = _cli(env, tmp_path, "--sgy-file", str(good))
    assert r.returncode == 3 and "school-state transition maintenance on" in r.stderr


# ---- review fixes ----------------------------------------------------------

def test_sync_in_flight_refuses_real_run_only(env):
    sp = _write_state(env)
    before = _sha(sp)
    sgy = _write_sgy(env, _sgy(("John S", [("HW", None)])))
    maintenance.mark_sync_running()
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 3 and "sync" in str(ei.value).lower()
    assert _sha(sp) == before and not (env / "archive").exists()
    assert _run(env, sgy, dry_run=True)["changed"] is True


def test_state_changed_during_run_aborts(env, monkeypatch):
    sp = _write_state(env, action_items=[_item("Jack", "Gone")])
    sgy = _write_sgy(env, _sgy(("Jack S", [("New", None)])))
    real = schoology_reset.write_atomic

    def racing(path, data, mode=0o600):
        real(path, data, mode)
        if Path(path).parent.name == "archive":
            sp.write_text(sp.read_text() + "\n ")

    monkeypatch.setattr(schoology_reset, "write_atomic", racing)
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 1 and "changed" in str(ei.value)
    assert "New" not in sp.read_text()


def _title_sgy(title, due="x"):
    return {"per_child": [{"child": {"name": "A B"}, "assignments": [{"title": title, "due_date": due}]}]}


@pytest.mark.parametrize("data", [
    _title_sgy(None), _title_sgy(""), _title_sgy(5), _title_sgy(["t"]),
    {"per_child": [{"child": {"name": "A B"}, "assignments": [{"due_date": "x"}]}]},
    {"per_child": [{"child": {"name": "A B"}, "grades": ["x"]}]},
    {"per_child": [{"child": {"name": "A B"}, "announcements": [1]}]},
    {"per_child": [{"child": {"name": "A B"}, "grades": None}]},
])
def test_field_type_validation_exit_2(env, data):
    sp = _write_state(env)
    before = _sha(sp)
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, _write_sgy(env, data))
    assert ei.value.code == 2
    assert _sha(sp) == before and not (env / "archive").exists()


def test_non_str_due_becomes_none(env):
    sp = _write_state(env)
    _run(env, _write_sgy(env, _title_sgy("T", due=12345)))
    assert _pending_sgy(json.loads(sp.read_text()))[0]["due"] is None


def test_deeply_nested_and_oversized_scrape_rejected(env):
    _write_state(env)
    deep = _write_sgy(env, {}, "deep.json")
    deep.write_text("[" * 200000)
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, deep)
    assert ei.value.code == 2
    big = env / "big.json"
    with big.open("wb") as f:
        f.truncate(schoology_reset.MAX_SGY_BYTES + 1)
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, big)
    assert ei.value.code == 2 and "large" in str(ei.value)


def test_unexpected_compute_error_is_exit_2(env, monkeypatch):
    _write_state(env)
    sgy = _write_sgy(env, _sgy(("John S", [("HW", None)])))

    def boom(*a, **k):
        raise TypeError("odd")

    monkeypatch.setattr(state, "_compact_grades", boom)
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 2


def _warn_sgy():
    return {**_sgy(("John S", [("HW", None)])), "warnings": ["rate limited " + "x" * 200, "b", "c", "d"]}


def test_warnings_block_real_run(env):
    sp = _write_state(env)
    before = _sha(sp)
    sgy = _write_sgy(env, _warn_sgy())
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 2 and "--allow-warnings" in str(ei.value)
    assert _sha(sp) == before


def test_warnings_allowed_and_dry_run(env):
    sgy = _write_sgy(env, _warn_sgy())
    _write_state(env)
    dry = _run(env, sgy, dry_run=True)
    assert dry["warnings_count"] == 4 and len(dry["warnings_preview"]) == 3
    assert all(len(w) <= 100 for w in dry["warnings_preview"])
    assert "warnings" in schoology_reset.render_text(dry).lower()
    assert _run(env, sgy, allow_warnings=True)["changed"] is True


def test_archive_never_clobbers_existing_file(env, monkeypatch):
    sp = _write_state(env, action_items=[_item("Jack", "Gone")])
    before = _sha(sp)
    sgy = _write_sgy(env, _sgy(("Jack S", [("New", None)])))
    arch = env / "archive"
    arch.mkdir(mode=0o700)
    victim = arch / "schoology-fixed.json"
    victim.write_text("precious")
    monkeypatch.setattr(schoology_reset, "_archive_name", lambda: "schoology-fixed.json")
    with pytest.raises(schoology_reset.ResetError) as ei:
        _run(env, sgy)
    assert ei.value.code == 1 and victim.read_text() == "precious"
    assert _sha(sp) == before
    assert [p.name for p in arch.iterdir()] == ["schoology-fixed.json"]


def test_archive_names_are_unique():
    assert schoology_reset._archive_name() != schoology_reset._archive_name()


def test_loose_archive_dir_is_tightened(env, tmp_path):
    _write_state(env, action_items=[_item("Jack", "Gone")])
    d = tmp_path / "loose"
    d.mkdir()
    os.chmod(d, 0o755)
    _run(env, _write_sgy(env, _sgy(("Jack S", [("New", None)]))), archive_dir=d)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_compute_reset_does_not_mutate_inputs():
    import copy
    old = {"schoology": {"John": {"a": 1}}, "action_items": [_item("Jack", "x"), _item("Jack", "y", source="email")]}
    pc = _sgy(("John S", [("x", "2026-11-01"), ("z", None)]))["per_child"]
    old0, pc0 = copy.deepcopy(old), copy.deepcopy(pc)
    schoology_reset.compute_reset(old, pc, "2026-10-22T00:00:00")
    assert old == old0 and pc == pc0


def test_unchanged_section_entry_keeps_old_updated():
    pc = _sgy(("Jack S", [("x", None)]))["per_child"]
    first = schoology_reset.compute_reset({"schoology": {}, "action_items": []}, pc, "2026-01-01T00:00:00")
    second = schoology_reset.compute_reset(first["new_state"], pc, "2026-06-01T00:00:00")
    assert second["new_state"]["schoology"]["Jack"]["updated"] == "2026-01-01T00:00:00"
    assert second["changed"] is False


def test_preserved_items_identical_and_ordered(env):
    items = [
        _item("Jack", "e1", source="email", due="Due Fri"), _item("Jack", "old1"),
        _item("Jack", "c1", status="completed"), _item("Jack", "old2"),
        _item("Penn", "m1", source="manual"), _item("Jack", "c2", source="email", status="completed"),
    ]
    sp = _write_state(env, action_items=items)
    _run(env, _write_sgy(env, _sgy(("Jack S", [("fresh", None)]))))
    after = json.loads(sp.read_text())["action_items"]
    keep = [i for i in items if i["source"] != "schoology" or i["status"] == "completed"]
    assert [i for i in after if i in keep] == keep and len(after) == len(keep) + 1


def test_odd_status_schoology_item_left_alone(env):
    odd = _item("Jack", "snoozed one", status="snoozed")
    sp = _write_state(env, action_items=[odd])
    _run(env, _write_sgy(env, _sgy(("Jack S", [("fresh", None)]))))
    assert odd in json.loads(sp.read_text())["action_items"]


def test_non_schoology_item_with_same_title_untouched(env):
    mail = _item("Jack", "HW", source="email")
    sp = _write_state(env, action_items=[mail])
    _run(env, _write_sgy(env, _sgy(("Jack S", [("HW", None)]))))
    after = json.loads(sp.read_text())["action_items"]
    assert mail in after and len(after) == 2 and len({i["id"] for i in after}) == 2


def test_kept_item_gets_due_fri_from_scrape(env):
    cur = _item("Jack", "HW", due=None, created="2026-09-09T00:00:00")
    sp = _write_state(env, action_items=[cur])
    _run(env, _write_sgy(env, _sgy(("Jack S", [("HW", "Due Fri")]))))
    (got,) = json.loads(sp.read_text())["action_items"]
    assert got["due"] == "Due Fri" and got["created"] == "2026-09-09T00:00:00"
