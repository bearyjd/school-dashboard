"""Service layer: taps, teacher confirmation, transactions, events, flags and outbox."""
import threading
from contextlib import contextmanager

import pytest

from recon.report_support import sgy
from recon.s4_support import NOW, event_rows, kid_actor, kw, open_conn, parent, stored
from school_dashboard.assignments import repo, service

DUE = "2026-11-20"


def task(**o):
    return {"kid": "Alex", "title": "Pack bag", "title_key": "pack bag", "class_key": "", "kind": "task",
            "due_date": "2026-11-19", **o}


def one(tmp_path, **o):
    return open_conn(tmp_path, [{"kid": "Alex", "title": "Essay", "title_key": "essay", "class_key": "math",
                                 "due_date": DUE, **o}])


def test_tap_done_sets_stage_event_and_flag(tmp_path):
    c = one(tmp_path)
    assert service.tap(c, 1, "done", parent("Dana"), **kw()) == 1
    row = stored(c, 1)
    assert row["stage"] == 2 and row["flag"] == "waiting_on_teacher"
    ev = [e for e in event_rows(c, 1) if e["kind"] == "stage"]
    assert (ev[-1]["setter"], ev[-1]["actor"], ev[-1]["from_value"], ev[-1]["to_value"]) == ("parent", "Dana", "1", "2")
    assert any(e["kind"] == "flag" and e["setter"] == "system" for e in event_rows(c, 1))


def test_kid_tap_is_attributed_to_the_kid(tmp_path):
    c = one(tmp_path)
    service.tap(c, 1, "turned_in", kid_actor("Alex"), **kw())
    ev = [e for e in event_rows(c, 1) if e["kind"] == "stage"][-1]
    assert (ev["setter"], ev["actor"]) == ("kid", "Alex") and stored(c, 1)["stage"] == 3


def test_actor_name_is_cleaned(tmp_path):
    c = one(tmp_path)
    service.tap(c, 1, "done", service.Actor("parent", "Da\x00  na"), **kw())
    assert [e for e in event_rows(c, 1) if e["kind"] == "stage"][-1]["actor"] == "Da na"


@pytest.mark.parametrize("actor", [("teacher", "Dana"), ("parent", ""), ("parent", " \x00 "), ("parent", "n" * 41)])
def test_bad_actor_is_a_conflict(tmp_path, actor):
    c = one(tmp_path)
    with pytest.raises(service.Conflict):
        service.tap(c, 1, "done", service.Actor(*actor), **kw())
    assert stored(c, 1)["stage"] == 1


def test_task_done_closes_and_undo_reopens(tmp_path):
    c = open_conn(tmp_path, [task()])
    service.tap(c, 1, "done", parent(), **kw())
    row = stored(c, 1)
    assert row["closed_at"] and row["flag"] is None and row["stage"] == 2
    service.tap(c, 1, "undo", parent(), **kw())
    row = stored(c, 1)
    assert row["closed_at"] is None and row["stage"] == 1 and row["flag"] == "do_tonight"


def test_practice_tap_is_a_conflict(tmp_path):
    c = open_conn(tmp_path, [task(kind="practice")])
    with pytest.raises(service.Conflict):
        service.tap(c, 1, "done", parent(), **kw())


@pytest.mark.parametrize("seed,action", [({"stage": 4}, "undo"), ({}, "bogus"), ({"stage": 1}, "undo")])
def test_invalid_transitions_conflict(tmp_path, seed, action):
    c = one(tmp_path, **seed)
    with pytest.raises(service.Conflict) as err:
        service.tap(c, 1, action, parent(), **kw())
    assert err.value.code == "invalid_transition"


def test_unknown_id_is_not_found(tmp_path):
    with pytest.raises(service.NotFound):
        service.tap(one(tmp_path), 99, "done", parent(), **kw())


def test_not_migrated(tmp_path):
    c = open_conn(tmp_path, migrated=False)
    with pytest.raises(repo.NotMigratedError):
        service.tap(c, 1, "done", parent(), **kw())


def test_merged_away_record_cannot_be_tapped(tmp_path):
    c = open_conn(tmp_path, [sgy("Essay", "Math 6", "s1"), sgy("Essay", "Math 6", "s2")])
    c.execute("UPDATE assignments SET merged_into = 1 WHERE id = 2")
    with pytest.raises(service.Conflict) as err:
        service.tap(c, 2, "done", parent(), **kw())
    assert err.value.code == "merged_away"


# ---- teacher confirmation ----
def test_teacher_confirmed_by_parent_closes_with_note(tmp_path):
    c = one(tmp_path, stage=2)
    service.teacher_confirmed(c, 1, "Ms Lee said\x00 got it", parent("Dana"), **kw())
    row = stored(c, 1)
    assert (row["stage"], row["received_evidence"], row["flag"]) == (4, "teacher_confirmed", None)
    assert row["closed_at"]
    ev = [e for e in event_rows(c, 1) if e["kind"] == "stage"][-1]
    assert ev["detail"] == "Ms Lee said got it" and ev["setter"] == "parent" and ev["actor"] == "Dana"


@pytest.mark.parametrize("note", ["ab", "  ab  ", "\x00\x00\x00\x00", "n" * 501, ""])
def test_teacher_confirmed_note_limits(tmp_path, note):
    c = one(tmp_path, stage=3)
    with pytest.raises(service.Conflict):
        service.teacher_confirmed(c, 1, note, parent(), **kw())
    assert stored(c, 1)["stage"] == 3


@pytest.mark.parametrize("seed,who", [({"stage": 1}, "p"), ({"stage": 4}, "p"), ({"stage": 2}, "k"),
                                      ({"stage": 2, "kind": "task"}, "p")])
def test_teacher_confirmed_refusals(tmp_path, seed, who):
    c = one(tmp_path, **seed)
    actor = parent() if who == "p" else kid_actor()
    with pytest.raises(service.Conflict):
        service.teacher_confirmed(c, 1, "confirmed by teacher", actor, **kw())


def test_no_service_call_sequence_reaches_stage_4_by_taps(tmp_path):
    c = one(tmp_path)
    for actor in (parent(), kid_actor()):
        for action in ("done", "turned_in", "undo", "done", "turned_in"):
            try:
                service.tap(c, 1, action, actor, **kw())
            except service.Conflict:
                pass
            assert stored(c, 1)["stage"] != 4


# ---- transactions, flags, outbox ----
@contextmanager
def _count(monkeypatch):
    calls = []
    real = repo.transaction

    @contextmanager
    def wrapper(conn):
        calls.append(1)
        with real(conn) as c:
            yield c
    monkeypatch.setattr(repo, "transaction", wrapper)
    yield calls


def test_one_transaction_per_call(tmp_path, monkeypatch):
    c = one(tmp_path)
    with _count(monkeypatch) as calls:
        service.tap(c, 1, "done", parent(), **kw())
        service.teacher_confirmed(c, 1, "teacher said ok", parent(), **kw())
    assert len(calls) == 2


def test_failure_in_recompute_rolls_everything_back(tmp_path, monkeypatch):
    from school_dashboard.assignments import ingest_kid
    c = one(tmp_path)
    before = len(event_rows(c, 1))
    monkeypatch.setattr(ingest_kid, "recompute_kid", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        service.tap(c, 1, "done", parent(), **kw())
    assert stored(c, 1)["stage"] == 1 and len(event_rows(c, 1)) == before and not c.in_transaction


def _disputed(tmp_path, **kwargs):
    return open_conn(tmp_path, [sgy("Essay", "Math 6", "s1", status="missing")], **kwargs)


def test_turn_in_against_portal_missing_enqueues_disputed_push(tmp_path):
    c = _disputed(tmp_path)
    c.execute("UPDATE assignments SET portal_status = 'missing' WHERE id = 1")
    service.tap(c, 1, "turned_in", parent(), **kw())
    row = c.execute("SELECT flag, status, reason FROM push_outbox").fetchone()
    assert tuple(row) == ("disputed", "pending", "transition")


def test_outbox_is_suppressed_before_the_baseline(tmp_path):
    c = _disputed(tmp_path, baseline=False)
    c.execute("UPDATE assignments SET portal_status = 'missing' WHERE id = 1")
    service.tap(c, 1, "turned_in", parent(), **kw())
    assert c.execute("SELECT status FROM push_outbox").fetchone()[0] == "suppressed"


@pytest.mark.parametrize("name", ["tap", "teacher_confirmed", "merge", "split", "remerge_kid", "set_class"])
def test_busy_error_propagates(tmp_path, name):
    from school_dashboard.assignments import schema
    c = open_conn(tmp_path, [sgy("Essay", "Math 6", "s1", stage=2), sgy("Essay", "Math 6", "s2")])
    path = c.execute("PRAGMA database_list").fetchone()["file"]
    c.execute("PRAGMA busy_timeout=50")
    other = schema.connect(path)
    other.execute("BEGIN IMMEDIATE")
    calls = {
        "tap": lambda: service.tap(c, 1, "done", parent(), **kw()),
        "teacher_confirmed": lambda: service.teacher_confirmed(c, 1, "teacher said", parent(), **kw()),
        "merge": lambda: service.merge(c, 2, 1, parent(), **kw()),
        "split": lambda: service.split(c, 1, 1, parent(), **kw()),
        "remerge_kid": lambda: service.remerge_kid(c, "Alex", None, **kw()),
        "set_class": lambda: service.set_class(c, "Alex", "Math 6", service.ClassSettings(), parent(), **kw()),
    }
    with pytest.raises(repo.BusyError):
        calls[name]()
    other.execute("ROLLBACK")
    other.close()
