"""Schoology auto-archive: counters, thresholds, guards, archive I/O, CLI integration."""
import argparse
import json
import logging
import os
import stat
from datetime import datetime, timedelta

import pytest

from school_dashboard import cli, maintenance, state
from school_dashboard import schoology_autoarchive as aa

T0 = datetime(2026, 10, 1, 8, 0, 0)


def _item(title, child="Ford", **kw):
    base = {"id": state._action_id("schoology", title, child), "child": child, "source": "schoology",
            "type": "assignment", "summary": title, "due": None, "status": "pending",
            "created": T0.isoformat()}
    return {**base, **kw}


def _st(*items):
    return {"action_items": list(items)}


def _ids(*titles, child="Ford"):
    return {state._action_id("schoology", t, child) for t in titles}


def _run(st, seen, now, trusted=True, child="Ford"):
    return aa.plan_autoarchive(st, {child: seen}, {child: trusted}, now)


def _get(st, title, child="Ford"):
    iid = state._action_id("schoology", title, child)
    return next(i for i in st["action_items"] if i["id"] == iid)


def test_present_item_resets_missed_and_sets_last_seen():
    st = _st(_item("A", missed=2, last_seen=T0.isoformat()))
    new, archived, _ = _run(st, _ids("A"), T0 + timedelta(days=1))
    assert _get(new, "A")["missed"] == 0
    assert _get(new, "A")["last_seen"] == (T0 + timedelta(days=1)).isoformat()
    assert archived == []


def test_trusted_absence_increments_and_does_not_mutate_input():
    st = _st(_item("A"), _item("B"))
    new, _, _ = _run(st, _ids("B"), T0 + timedelta(days=1))
    assert _get(new, "A")["missed"] == 1
    assert "missed" not in st["action_items"][0]


def test_legacy_absent_item_is_seeded_with_now_not_created():
    now = T0 + timedelta(days=30)
    new, _, _ = _run(_st(_item("A"), _item("B")), _ids("B"), now)
    assert _get(new, "A")["missed"] == 1
    assert _get(new, "A")["last_seen"] == now.isoformat()


def test_legacy_item_needs_days_after_seeding_even_if_old_and_missed():
    now = T0 + timedelta(days=60)
    new, archived, _ = _run(_st(_item("A", missed=2), _item("B")), _ids("B"), now)
    assert archived == [] and _get(new, "A")["missed"] == 3


def test_unparseable_last_seen_falls_back_to_now():
    now = T0 + timedelta(days=1)
    new, _, _ = _run(_st(_item("A", last_seen="garbage"), _item("B")), _ids("B"), now)
    assert _get(new, "A")["last_seen"] == now.isoformat()


def test_untrusted_scrape_never_increments_but_refreshes_present():
    st = _st(_item("A", missed=1, last_seen=T0.isoformat()), _item("B", missed=2, last_seen=T0.isoformat()))
    new, archived, _ = _run(st, _ids("B"), T0 + timedelta(days=9), trusted=False)
    assert _get(new, "A")["missed"] == 1
    assert _get(new, "B")["missed"] == 0
    assert archived == []


def test_suspicious_empty_scrape_not_trusted_and_warns():
    items = [_item(t, missed=2, last_seen=T0.isoformat()) for t in "ABC"]
    new, archived, warns = _run(_st(*items), set(), T0 + timedelta(days=9))
    assert archived == [] and all(_get(new, t)["missed"] == 2 for t in "ABC")
    assert any("empty scrape" in w for w in warns)


def test_empty_scrape_with_few_pending_is_trusted():
    _, archived, _ = _run(_st(_item("A", missed=2, last_seen=T0.isoformat())), set(), T0 + timedelta(days=9))
    assert len(archived) == 1


def test_child_absent_from_scrape_untouched():
    st = _st(_item("A", child="Jack", missed=2, last_seen=T0.isoformat()))
    new, archived, _ = aa.plan_autoarchive(st, {"Ford": set()}, {"Ford": True}, T0 + timedelta(days=9))
    assert new == st and archived == []


def test_threshold_needs_both_misses_and_days():
    item = _item("A", missed=2, last_seen=T0.isoformat())
    keep = _item("K")
    # 3rd miss but only 1 day absent -> kept
    new, archived, _ = _run(_st(item, keep), _ids("K"), T0 + timedelta(days=1))
    assert archived == [] and _get(new, "A")["missed"] == 3
    # enough days but only 2nd miss -> kept
    low = _item("A", missed=1, last_seen=T0.isoformat())
    _, archived, _ = _run(_st(low, keep), _ids("K"), T0 + timedelta(days=10))
    assert archived == []
    # both -> archived with reason
    new, archived, _ = _run(_st(item, keep), _ids("K"), T0 + timedelta(days=3))
    assert [e["item"]["summary"] for e in archived] == ["A"]
    assert archived[0]["reason"] == "absent from 3 scrapes over 3 days"
    assert archived[0]["archived_at"] == (T0 + timedelta(days=3)).isoformat()
    assert [i["summary"] for i in new["action_items"]] == ["K"]


def test_mass_change_guard_keeps_everything():
    gone = [_item(f"G{n}", missed=2, last_seen=T0.isoformat()) for n in range(14)]
    keep = [_item(f"K{n}") for n in range(6)]
    seen = _ids(*[f"K{n}" for n in range(6)])
    new, archived, warns = _run(_st(*gone, *keep), seen, T0 + timedelta(days=5))
    assert archived == [] and len(new["action_items"]) == 20
    assert all(_get(new, f"G{n}")["missed"] == 2 for n in range(14))
    assert any("mass change suspected" in w for w in warns)


def test_small_delta_below_guard_proceeds():
    gone = [_item(f"G{n}", missed=2, last_seen=T0.isoformat()) for n in range(5)]
    keep = [_item(f"K{n}") for n in range(20)]
    _, archived, warns = _run(_st(*gone, *keep), _ids(*[f"K{n}" for n in range(20)]), T0 + timedelta(days=5))
    assert len(archived) == 5 and not warns


def test_completed_and_non_schoology_untouched():
    done = _item("D", status="completed", missed=9, last_seen=T0.isoformat())
    mail = {**_item("M"), "source": "email"}
    st = _st(done, mail, _item("K"))
    new, archived, _ = _run(st, _ids("K"), T0 + timedelta(days=30))
    assert archived == [] and new["action_items"][:2] == [done, mail]


def test_scrape_view_trust_rules(tmp_path):
    f = tmp_path / "s.json"
    entry = {"child": {"name": "Ford Smith"}, "assignments": [{"title": "A"}]}
    f.write_text(json.dumps({"per_child": [entry, {"child": {}}, "junk"]}))
    seen, trusted, digest = aa.scrape_view(str(f))
    assert len(digest) == 64
    assert seen == {"Ford": _ids("A")} and trusted == {"Ford": True}
    f.write_text(json.dumps({"per_child": [entry], "warnings": ["rate limited"]}))
    assert aa.scrape_view(str(f))[1] == {"Ford": False}
    f.write_text("not json")
    assert aa.scrape_view(str(f)) == ({}, {}, None)
    assert aa.scrape_view(str(tmp_path / "missing.json")) == ({}, {}, None)


def _write_sgy(path, titles, warnings=None, salt=0):
    data = {"salt": salt, "per_child": [{"child": {"name": "Ford Smith"}, "assignments": [{"title": t} for t in titles]}]}
    if warnings:
        data["warnings"] = warnings
    path.write_text(json.dumps(data))


def _due_state(now):
    old = (now - timedelta(days=10)).isoformat()
    return _st(_item("A", missed=2, last_seen=old), _item("K"))


def test_run_writes_archive_with_perms_before_removal(state_env, tmp_path):
    sgy = tmp_path / "sgy.json"
    _write_sgy(sgy, ["K"])
    now = datetime.now()
    new, n = aa.run_autoarchive(_due_state(now), str(sgy), state_env / "school-state.json", now)
    assert n == 1 and [i["summary"] for i in new["action_items"]] == ["K"]
    arch = state_env / "archive" / f"schoology-auto-{now:%Y-%m}.jsonl"
    assert stat.S_IMODE(arch.stat().st_mode) == 0o600
    assert stat.S_IMODE(arch.parent.stat().st_mode) == 0o700
    line = json.loads(arch.read_text().splitlines()[0])
    assert line["item"]["summary"] == "A" and line["reason"].startswith("absent from 3 scrapes")
    aa.run_autoarchive(_due_state(now), str(sgy), state_env / "school-state.json", now)
    assert len(arch.read_text().splitlines()) == 1  # re-run after a crash does not duplicate


def test_archive_failure_keeps_items(state_env, tmp_path, monkeypatch):
    sgy = tmp_path / "sgy.json"
    _write_sgy(sgy, ["K"])

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(aa, "append_archive", boom)
    now = datetime.now()
    new, n = aa.run_autoarchive(_due_state(now), str(sgy), state_env / "school-state.json", now)
    assert n == 0 and {i["summary"] for i in new["action_items"]} == {"A", "K"}
    assert _get(new, "A")["missed"] == 2  # counters not advanced
    assert aa.STATE_KEY not in new  # hash not advanced -> next run retries


def test_maintenance_active_is_noop(state_env, tmp_path):
    sgy = tmp_path / "sgy.json"
    _write_sgy(sgy, ["K"])
    maintenance.enable("test")
    st = _due_state(datetime.now())
    new, n = aa.run_autoarchive(st, str(sgy), state_env / "school-state.json")
    assert new is st and n == 0 and not (state_env / "archive").exists()


def _args(state_file, sgy, tmp_path):
    return argparse.Namespace(state_file=str(state_file), ixl_dir=str(tmp_path / "noixl"), sgy_file=str(sgy))


def _shift_last_seen(state_file, days):
    s = json.loads(state_file.read_text())
    for i in s["action_items"]:
        if i.get("last_seen"):
            i["last_seen"] = (datetime.fromisoformat(i["last_seen"]) - timedelta(days=days)).isoformat()
    state_file.write_text(json.dumps(s))


def test_cli_update_archives_after_third_trusted_scrape(state_env, tmp_path, capsys):
    sf, sgy = state_env / "school-state.json", tmp_path / "sgy.json"
    _write_sgy(sgy, ["Gone", "Stay"])
    cli.cmd_update(_args(sf, sgy, tmp_path))
    assert "auto-archived 0" in capsys.readouterr().err
    for n in (1, 2):  # misses 1 and 2, each a distinct scrape
        _write_sgy(sgy, ["Stay"], salt=n)
        _shift_last_seen(sf, 2)
        cli.cmd_update(_args(sf, sgy, tmp_path))
    pending = {i["summary"]: i for i in json.loads(sf.read_text())["action_items"]}
    assert pending["Gone"]["missed"] == 2
    _shift_last_seen(sf, 2)  # 6 days absent in total
    _write_sgy(sgy, ["Stay"], salt=3)
    cli.cmd_update(_args(sf, sgy, tmp_path))
    err = capsys.readouterr().err
    assert "auto-archived 1" in err and "stale items pruned" in err
    assert [i["summary"] for i in json.loads(sf.read_text())["action_items"]] == ["Stay"]
    arch = next((state_env / "archive").glob("schoology-auto-*.jsonl"))
    assert json.loads(arch.read_text())["item"]["summary"] == "Gone"


def test_cli_update_with_warnings_never_archives(state_env, tmp_path):
    sf, sgy = state_env / "school-state.json", tmp_path / "sgy.json"
    _write_sgy(sgy, ["Gone", "Stay"])
    cli.cmd_update(_args(sf, sgy, tmp_path))
    _write_sgy(sgy, ["Stay"], warnings=["rate limited"])
    for n in range(4):
        _write_sgy(sgy, ["Stay"], warnings=["rate limited"], salt=n)
        _shift_last_seen(sf, 5)
        cli.cmd_update(_args(sf, sgy, tmp_path))
    assert {i["summary"] for i in json.loads(sf.read_text())["action_items"]} == {"Gone", "Stay"}
    assert not (state_env / "archive").exists()


def test_same_scrape_five_times_counts_one_miss(state_env, tmp_path):
    sf, sgy = state_env / "school-state.json", tmp_path / "sgy.json"
    _write_sgy(sgy, ["Gone", "Stay"])
    cli.cmd_update(_args(sf, sgy, tmp_path))
    _write_sgy(sgy, ["Stay"])
    for _ in range(5):
        _shift_last_seen(sf, 5)
        cli.cmd_update(_args(sf, sgy, tmp_path))
    s = json.loads(sf.read_text())
    assert {i["summary"]: i["missed"] for i in s["action_items"]} == {"Gone": 1, "Stay": 0}
    assert len(s[aa.STATE_KEY]["hash"]) == 64 and s[aa.STATE_KEY]["processed_at"]
    _write_sgy(sgy, ["Stay"], salt=1)  # a changed file counts again
    cli.cmd_update(_args(sf, sgy, tmp_path))
    assert {i["summary"]: i["missed"] for i in json.loads(sf.read_text())["action_items"]}["Gone"] == 2


def test_same_scrape_never_archives_even_when_overdue(state_env, tmp_path):
    sgy = tmp_path / "sgy.json"
    _write_sgy(sgy, ["K"])
    now = datetime.now()
    st = _due_state(now)
    digest = aa.scrape_view(str(sgy))[2]
    st = {**st, aa.STATE_KEY: {"hash": digest, "processed_at": now.isoformat()}}
    new, n = aa.run_autoarchive(st, str(sgy), state_env / "school-state.json", now)
    assert n == 0 and _get(new, "A")["missed"] == 2 and not (state_env / "archive").exists()
    assert new[aa.STATE_KEY]["hash"] == digest


def test_archive_failure_does_not_advance_hash_then_retry_succeeds(state_env, tmp_path, monkeypatch):
    sgy = tmp_path / "sgy.json"
    _write_sgy(sgy, ["K"])
    now = datetime.now()
    real = aa.append_archive
    monkeypatch.setattr(aa, "append_archive", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    st = _due_state(now)
    failed, n = aa.run_autoarchive(st, str(sgy), state_env / "school-state.json", now)
    assert n == 0 and aa.STATE_KEY not in failed
    monkeypatch.setattr(aa, "append_archive", real)
    ok, n = aa.run_autoarchive(failed, str(sgy), state_env / "school-state.json", now)
    assert n == 1 and aa.STATE_KEY in ok


def test_reset_preserves_scrape_key(state_env, tmp_path):
    from school_dashboard import schoology_reset
    sgy = tmp_path / "sgy.json"
    _write_sgy(sgy, ["K"])
    sf = state_env / "school-state.json"
    sf.write_text(json.dumps({**state._empty_state(), aa.STATE_KEY: {"hash": "x", "processed_at": "y"}}))
    maintenance.enable("t")
    schoology_reset.run_reset(sgy, state_file=sf)
    assert json.loads(sf.read_text())[aa.STATE_KEY] == {"hash": "x", "processed_at": "y"}


# ── review round 2 ───────────────────────────────────────────────────────────

def _due_items(n_due, n_keep):
    due = [_item(f"G{n}", missed=2, last_seen=T0.isoformat()) for n in range(n_due)]
    keep = [_item(f"K{n}") for n in range(n_keep)]
    return _st(*due, *keep), _ids(*[f"K{n}" for n in range(n_keep)], "unrelated")


@pytest.mark.parametrize("n_due,n_keep,archived_n", [
    (3, 3, 3),   # exactly 50% -> allowed
    (4, 0, 0),   # 4 of 4 (all pending, >=2) -> blocked
    (2, 0, 0),   # 2 of 2 -> blocked
    (1, 0, 1),   # 1 of 1 -> allowed
    (3, 2, 0),   # 3 of 5 > 50% and >=3 -> blocked
    (2, 1, 2),   # 2 of 3 > 50% but < 3 -> allowed
])
def test_mass_guard_boundaries(n_due, n_keep, archived_n):
    st, seen = _due_items(n_due, n_keep)
    new, archived, warns = _run(st, seen, T0 + timedelta(days=5))
    assert len(archived) == archived_n
    if archived_n == 0:
        assert len(new["action_items"]) == n_due + n_keep
        assert any("schoology reset --dry-run" in w for w in warns)
        assert all(_get(new, f"G{n}")["missed"] == 2 for n in range(n_due))


def test_future_due_item_never_counts_or_archives():
    now = T0 + timedelta(days=30)
    fut = _item("F", due=(now + timedelta(days=2)).strftime("%Y-%m-%d"), missed=5, last_seen=T0.isoformat())
    undated = _item("U", due="Due Fri", missed=0)
    new, archived, _ = _run(_st(fut, undated, _item("K")), _ids("K"), now)
    assert archived == [] and _get(new, "F")["missed"] == 5
    assert _get(new, "U")["missed"] == 1  # unparseable due still counts
    past = _item("P", due=(now - timedelta(days=2)).strftime("%Y-%m-%d"))
    new, _, _ = _run(_st(past, _item("K")), _ids("K"), now)
    assert _get(new, "P")["missed"] == 1


def test_aware_and_garbage_fields_do_not_raise():
    now = T0 + timedelta(days=10)
    items = [
        _item("A", last_seen="2026-10-01T08:00:00+00:00", missed=2),
        _item("B", missed="abc", last_seen=T0.isoformat()),
        _item("C", missed=None, created="2026-10-01T08:00:00Z"),
        _item("D", last_seen="2026-10-01T08:00:00-05:00"),  # present path
        _item("K"),
    ]
    new, archived, _ = _run(_st(*items), _ids("K", "D"), now)
    assert [e["item"]["summary"] for e in archived] == ["A"]  # aware stamp parsed, 3 misses over 9 days
    assert _get(new, "B")["missed"] == 1 and _get(new, "C")["missed"] == 1
    assert _get(new, "D")["missed"] == 0 and _get(new, "D")["last_seen"] == now.isoformat()


@pytest.mark.parametrize("warnings,clean", [
    (None, True), ([], True), ("", False), (0, False), ({}, False), ("x", False), (["w"], False), (5, False),
])
def test_scrape_view_warnings_trust(tmp_path, warnings, clean):
    f = tmp_path / "s.json"
    data = {"per_child": [{"child": {"name": "Ford"}, "assignments": [{"title": "A"}]}]}
    if warnings is not None:
        data["warnings"] = warnings
    f.write_text(json.dumps(data))
    assert aa.scrape_view(str(f))[1] == {"Ford": clean}


def test_scrape_view_size_cap(tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    _write_sgy(f, ["A"])
    monkeypatch.setattr(aa, "MAX_SCRAPE_BYTES", 10)
    assert aa.scrape_view(str(f)) == ({}, {}, None)


def test_scrape_view_merges_entries_for_same_child_and_aliases(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"children": {}, "name_aliases": {"pennington": "Penn"}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.setattr(state, "_config_cache", None)
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"per_child": [
        {"child": {"name": "Pennington Smith"}, "assignments": [{"title": "A"}]},
        {"child": {"name": "Penn Smith"}, "assignments": [{"title": "B"}]},
        {"child": {"name": "Penn X"}, "assignments": "bad"},
    ]}))
    seen, trusted, _ = aa.scrape_view(str(f))
    assert seen == {"Penn": _ids("A", "B", child="Penn")} and trusted == {"Penn": True}
    f.write_text(json.dumps({"warnings": ["w"], "per_child": [
        {"child": {"name": "Penn"}, "assignments": []}, {"child": {"name": "Penn"}, "assignments": []}]}))
    assert aa.scrape_view(str(f))[1] == {"Penn": False}
    monkeypatch.setattr(state, "_config_cache", None)


def test_unchanged_hash_refreshes_last_seen_but_never_counts(state_env, tmp_path):
    sgy = tmp_path / "sgy.json"
    _write_sgy(sgy, ["K"])
    now = datetime.now()
    digest = aa.scrape_view(str(sgy))[2]
    st = {**_st(_item("K", last_seen=T0.isoformat(), missed=1), _item("A", missed=1, last_seen=T0.isoformat())),
          aa.STATE_KEY: {"hash": digest, "processed_at": "x"}}
    new, n = aa.run_autoarchive(st, str(sgy), state_env / "school-state.json", now)
    assert n == 0 and _get(new, "K")["last_seen"] == now.isoformat() and _get(new, "K")["missed"] == 0
    assert _get(new, "A")["missed"] == 1


def test_logs_contain_counts_only(state_env, tmp_path, caplog):
    sgy = tmp_path / "sgy.json"
    _write_sgy(sgy, ["K0"])
    st, _ = _due_items(14, 1)
    with caplog.at_level(logging.WARNING):
        aa.run_autoarchive(st, str(sgy), state_env / "school-state.json", T0 + timedelta(days=5))
    assert "mass change" in caplog.text
    assert "Ford" not in caplog.text and "G0" not in caplog.text and "K0" not in caplog.text


# ── archive file I/O ─────────────────────────────────────────────────────────

def _entry(title, child="Ford"):
    return {"archived_at": "t", "reason": "r", "item": _item(title, child)}


def test_append_archive_handles_short_writes(tmp_path, monkeypatch):
    real = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real(fd, bytes(data)[:3]))
    path = tmp_path / "archive" / "a.jsonl"
    aa.append_archive(path, [_entry("A"), _entry("B")])
    monkeypatch.undo()
    assert [json.loads(ln)["item"]["summary"] for ln in path.read_text().splitlines()] == ["A", "B"]


def test_append_archive_zero_write_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "write", lambda fd, data: 0)
    with pytest.raises(OSError):
        aa.append_archive(tmp_path / "archive" / "a.jsonl", [_entry("A")])


def test_append_archive_fsyncs_file_and_directory(tmp_path, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(os.fstat(fd).st_mode), real(fd))[1])
    aa.append_archive(tmp_path / "archive" / "a.jsonl", [_entry("A")])
    assert any(stat.S_ISDIR(m) for m in calls) and any(stat.S_ISREG(m) for m in calls)


def test_append_archive_separates_torn_last_line(tmp_path):
    path = tmp_path / "archive" / "a.jsonl"
    aa.append_archive(path, [_entry("A")])
    with open(path, "a") as f:
        f.write('{"archived_at": "torn')
    aa.append_archive(path, [_entry("B")])
    lines = path.read_text().splitlines()
    assert json.loads(lines[-1])["item"]["summary"] == "B" and len(lines) == 3


def test_append_archive_skips_already_archived_ids(tmp_path):
    path = tmp_path / "archive" / "a.jsonl"
    aa.append_archive(path, [_entry("A")])
    with open(path, "a") as f:
        f.write("not json\n")
    aa.append_archive(path, [_entry("A"), _entry("B")])
    ok = [json.loads(ln)["item"]["summary"] for ln in path.read_text().splitlines() if ln.startswith("{")]
    assert ok == ["A", "B"]


def test_append_archive_rejects_symlinked_dir(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    (tmp_path / "archive").symlink_to(real)
    with pytest.raises(OSError):
        aa.append_archive(tmp_path / "archive" / "a.jsonl", [_entry("A")])
    assert not list(real.iterdir())


def test_append_archive_rejects_symlinked_file(tmp_path):
    d = tmp_path / "archive"
    d.mkdir(mode=0o700)
    target = tmp_path / "target.jsonl"
    target.write_text("")
    (d / "a.jsonl").symlink_to(target)
    with pytest.raises(OSError):
        aa.append_archive(d / "a.jsonl", [_entry("A")])
    assert target.read_text() == ""


def test_append_archive_tightens_loose_dir(tmp_path):
    d = tmp_path / "archive"
    d.mkdir()
    os.chmod(d, 0o755)
    aa.append_archive(d / "a.jsonl", [_entry("A")])
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_symlinked_archive_dir_keeps_items_pending(state_env, tmp_path):
    elsewhere = tmp_path / "x"
    elsewhere.mkdir()
    (state_env / "archive").symlink_to(elsewhere)
    sgy = tmp_path / "sgy.json"
    _write_sgy(sgy, ["K"])
    now = datetime.now()
    new, n = aa.run_autoarchive(_due_state(now), str(sgy), state_env / "school-state.json", now)
    assert n == 0 and {i["summary"] for i in new["action_items"]} == {"A", "K"}


# ── round 3 ──────────────────────────────────────────────────────────────────

def test_archive_dedupe_key_includes_last_seen(tmp_path):
    path = tmp_path / "archive" / "a.jsonl"
    first = {"archived_at": "t", "reason": "r", "item": _item("A", last_seen="2026-10-01T08:00:00")}
    again = {"archived_at": "t", "reason": "r", "item": _item("A", last_seen="2026-11-01T08:00:00")}
    aa.append_archive(path, [first])
    aa.append_archive(path, [first])  # crash retry of the same archival
    assert len(path.read_text().splitlines()) == 1
    aa.append_archive(path, [again])  # archived, re-added, archived again
    assert len(path.read_text().splitlines()) == 2


def _args_cli(sf, sgy, tmp_path):
    return argparse.Namespace(state_file=str(sf), ixl_dir=str(tmp_path / "noixl"), sgy_file=str(sgy))


def test_corrupt_state_file_is_never_overwritten(state_env, tmp_path, capsys):
    sf, sgy = state_env / "school-state.json", tmp_path / "sgy.json"
    sf.write_bytes(b'{"action_items": [ truncated')
    _write_sgy(sgy, ["A"])
    with pytest.raises(SystemExit) as exc:
        cli.cmd_update(_args_cli(sf, sgy, tmp_path))
    assert exc.value.code == 1
    assert sf.read_bytes() == b'{"action_items": [ truncated'
    assert not (state_env / "archive").exists()
    err = capsys.readouterr().err
    assert str(sf) in err and "backups" in err
    add = argparse.Namespace(state_file=str(sf), child="Ford", summary="x", due=None, source=None, type=None)
    with pytest.raises(SystemExit):
        cli.cmd_action_add(add)
    with pytest.raises(SystemExit):
        cli.cmd_action_complete(argparse.Namespace(state_file=str(sf), id="abc"))
    assert sf.read_bytes() == b'{"action_items": [ truncated'


def test_non_object_state_file_is_refused(state_env, tmp_path):
    sf, sgy = state_env / "school-state.json", tmp_path / "sgy.json"
    sf.write_text("[1, 2]")
    _write_sgy(sgy, ["A"])
    with pytest.raises(SystemExit):
        cli.cmd_update(_args_cli(sf, sgy, tmp_path))
    assert sf.read_text() == "[1, 2]"


def test_missing_state_file_is_created(state_env, tmp_path):
    sf, sgy = state_env / "school-state.json", tmp_path / "sgy.json"
    _write_sgy(sgy, ["A"])
    cli.cmd_update(_args_cli(sf, sgy, tmp_path))
    assert [i["summary"] for i in json.loads(sf.read_text())["action_items"]] == ["A"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_state_dir_exits_cleanly(tmp_path, capsys):
    d = tmp_path / "ro"
    d.mkdir()
    sf = d / "school-state.json"
    d.chmod(0o500)
    try:
        add = argparse.Namespace(state_file=str(sf), child="Ford", summary="x", due=None, source=None, type=None)
        with pytest.raises(SystemExit) as exc:
            cli.cmd_action_add(add)
        assert exc.value.code == 1
    finally:
        d.chmod(0o700)
    err = capsys.readouterr().err
    assert err.startswith("Error:") and "Traceback" not in err and len(err.strip().splitlines()) == 1
