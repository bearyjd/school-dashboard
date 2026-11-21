"""Pins for title truncation, untitled rows and the IXL file merge order."""
import os

import pytest

from recon.conftest import NOW
from school_dashboard.assignments import ingest_io
from school_dashboard.assignments.migrate_plan import plan_migration


def _plan(acts):
    return plan_migration([], acts, now=NOW, resolve_kid=lambda n: n)


def _act(i, summary, child="Alex", due=None):
    return {"id": f"a{i}", "child": child, "source": "schoology", "type": "task", "summary": summary,
            "due": due, "status": "pending", "created": "2026-11-01T07:00:00"}


@pytest.mark.parametrize("ch", ["a", "é", "日", "\U0001f600"])
def test_300_characters_kept_whole_301_cut_to_299_plus_ellipsis(ch):
    keep = _plan([_act(1, ch * 300)]).records[0].state.title
    cut = _plan([_act(2, ch * 301)])
    assert keep == ch * 300 and len(keep) == 300
    title = cut.records[0].state.title
    assert title == ch * 299 + "…" and len(title) == 300
    assert any("shortened" in w for w in cut.warnings)


def test_two_untitled_rows_for_one_kid_and_due_are_never_merged():
    legacy = {"id": 1, "child": "Alex", "title": "", "due_date": "2026-11-20", "type": "task",
              "source": "schoology", "completed": 0, "created_at": "2026-11-01 07:00:00"}
    plan = plan_migration([legacy], [_act(1, "", due="2026-11-20"), _act(2, "", due="2026-11-20")], now=NOW,
                          resolve_kid=lambda n: n)
    assert len(plan.records) == 3 and plan.merged == ()


def _ixl_file(folder, name, subject_counts, mtime):
    import json
    path = folder / f"{name}-assigned.json"
    path.write_text(json.dumps({"status": "ok", "warnings": [], "errors": [], "remaining": [],
                                "totals": {"Math": subject_counts}}))
    os.utime(path, (mtime, mtime))


def test_newer_ixl_file_wins_per_subject_even_when_its_name_sorts_first(tmp_path):
    older = {"assigned": 4, "done": 1, "in_progress": 0, "not_started": 0, "remaining": 3}
    newer = {"assigned": 4, "done": 3, "in_progress": 0, "not_started": 0, "remaining": 1}
    _ixl_file(tmp_path, "alpha", newer, 2_000_000_000)  # sorts first by name, newest by mtime
    _ixl_file(tmp_path, "beta", older, 1_900_000_000)
    result, _, problem = ingest_io._load_ixl(str(tmp_path), lambda n: "Sam", NOW)
    assert problem is None and result is not None
    assert [r.raw_status for r in result.records] == ["1 remaining"]
