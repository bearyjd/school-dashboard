"""Write operations for the v2 API: one transaction per call (read, write, recompute flags, enqueue pushes).

Errors: NotFound (unknown record), Conflict(code) (state or role forbids it), repo.BusyError (lock not granted),
repo.NotMigratedError. Logs carry counts and type names only.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any, Mapping, Sequence

from . import repo, stages
from .adapters.base import clean_text
from .service_classes import set_class
from .service_merge import merge, remerge_kid
from .service_split import split
from .service_types import (Actor, ClassSettings, Conflict, NotFound, RemergeResult, Who, load_open, recompute,
                            who_from, write_txn)

__all__ = ["Actor", "ClassSettings", "Conflict", "NotFound", "RemergeResult", "merge", "remerge_kid", "set_class",
           "split", "tap", "teacher_confirmed"]

logger = logging.getLogger(__name__)
Rows = Sequence[Mapping[str, Any]]


def _stage_event(conn: sqlite3.Connection, old: Any, new: Any, who: Who, now: datetime, detail: str) -> None:
    repo.append_event(conn, new.id, at=now, setter=who.setter, actor=who.actor, kind="stage",
                      from_value=str(old.stage), to_value=str(new.stage), detail=detail)


def tap(conn: sqlite3.Connection, assignment_id: int, action: str, actor: Actor, *, now: datetime,
        no_school_rows: Rows) -> int:
    who = who_from(actor)
    with write_txn(conn):
        state = load_open(conn, assignment_id)
        try:
            new = stages.apply_tap(state, action, who.setter, now)
        except stages.StageError as exc:
            raise Conflict("invalid_transition") from exc
        repo.update_assignment(conn, new, now=now)
        _stage_event(conn, state, new, who, now, action)
        recompute(conn, state.kid, now, no_school_rows)
    return assignment_id


def teacher_confirmed(conn: sqlite3.Connection, assignment_id: int, note: str, actor: Actor, *, now: datetime,
                      no_school_rows: Rows) -> int:
    who = who_from(actor, parent_only=True)
    text = clean_text(note, 501)
    if not 3 <= len(text) <= 500:
        raise Conflict("bad_note")
    with write_txn(conn):
        state = load_open(conn, assignment_id)
        try:
            new = stages.confirm_by_teacher(state, who.setter, now)
        except stages.StageError as exc:
            raise Conflict("invalid_transition") from exc
        repo.update_assignment(conn, new, now=now)
        _stage_event(conn, state, new, who, now, text)
        recompute(conn, state.kid, now, no_school_rows)
    return assignment_id
