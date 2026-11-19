"""Task / practice taps and teacher confirmation (pure)."""
from itertools import product

import pytest

from recon.support import local, mk_link, mk_state
from school_dashboard.assignments import stages

NOW = local(2026, 11, 18, 17)


def test_task_done_closes_without_stage_4():
    out = stages.apply_tap(mk_state(kind="task"), "done", "parent", NOW)
    assert (out.stage, out.closed_at) == (2, NOW)


def test_task_undo_reopens():
    done = stages.apply_tap(mk_state(kind="task"), "done", "kid", NOW)
    out = stages.apply_tap(done, "undo", "kid", NOW)
    assert (out.stage, out.closed_at) == (1, None)


@pytest.mark.parametrize("state_kw,action", [({"kind": "task"}, "turned_in"), ({"kind": "task"}, "undo"),
                                              ({"kind": "task"}, "bogus")])
def test_bad_task_taps(state_kw, action):
    with pytest.raises(stages.StageError):
        stages.apply_tap(mk_state(**state_kw), action, "parent", NOW)


def test_done_on_closed_task_is_rejected():
    closed = stages.apply_tap(mk_state(kind="task"), "done", "parent", NOW)
    with pytest.raises(stages.StageError):
        stages.apply_tap(closed, "done", "parent", NOW)


@pytest.mark.parametrize("action", ["done", "turned_in", "undo"])
@pytest.mark.parametrize("stage", [1, 2, 3])
def test_practice_taps_are_rejected(action, stage):
    with pytest.raises(stages.StageError):
        stages.apply_tap(mk_state(kind="practice", stage=stage), action, "parent", NOW)


def test_assignment_taps_unchanged():
    assert stages.apply_tap(mk_state(), "turned_in", "kid", NOW).stage == 3
    assert stages.apply_tap(mk_state(), "turned_in", "kid", NOW).closed_at is None


@pytest.mark.parametrize("stage", [2, 3])
def test_confirm_by_teacher_closes(stage):
    out = stages.confirm_by_teacher(mk_state(stage=stage), "parent", NOW)
    assert (out.stage, out.received_evidence, out.closed_at) == (4, "teacher_confirmed", NOW)


@pytest.mark.parametrize("kw,setter", [({"stage": 1}, "parent"), ({"stage": 4}, "parent"), ({"stage": 2}, "kid"),
                                        ({"stage": 2}, "portal"), ({"stage": 2, "kind": "task"}, "parent"),
                                        ({"stage": 2, "kind": "practice"}, "parent")])
def test_confirm_by_teacher_refusals(kw, setter):
    with pytest.raises(stages.StageError):
        stages.confirm_by_teacher(mk_state(**kw), setter, NOW)


def test_live_missing_link_reopens_a_teacher_confirmed_record():
    state = stages.confirm_by_teacher(mk_state(stage=3), "parent", NOW)
    assert stages.portal_decision(state, [mk_link(norm_status="missing")], NOW.date())[0] == 1
    assert stages.portal_decision(state, [mk_link(norm_status="open")], NOW.date()) == (4, "teacher_confirmed")


def test_no_tap_sequence_reaches_stage_4():
    for kind, stage in product(("assignment", "task", "practice"), (1, 2, 3)):
        frontier = [mk_state(kind=kind, stage=stage)]
        for _ in range(4):
            nxt = []
            for st, action, who in product(frontier, ("done", "turned_in", "undo", "x"), ("parent", "kid")):
                try:
                    out = stages.apply_tap(st, action, who, NOW)
                except stages.StageError:
                    continue
                assert out.stage != 4
                nxt.append(out)
            frontier = nxt[:40]
