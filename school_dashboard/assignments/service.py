"""Write operations for the v2 API: one transaction per call (read, write, recompute flags, enqueue pushes).

Errors: NotFound (unknown record), Conflict(code) (state or role forbids it), repo.BusyError (lock not granted),
repo.NotMigratedError. Logs carry counts and type names only.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping, Sequence

from . import repo, stages
from .adapters.base import clean_text
from .normalize import class_key
from .service_merge import merge, remerge_in_txn, remerge_kid, split
from .service_types import Actor, ClassSettings, Conflict, NotFound, Who, load_open, recompute, who_from, write_txn

__all__ = ["Actor", "ClassSettings", "Conflict", "NotFound", "merge", "remerge_kid", "set_class", "split", "tap",
           "teacher_confirmed"]

logger = logging.getLogger(__name__)
Rows = Sequence[Mapping[str, Any]]
MAX_ALIASES = 20
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


def _clean_aliases(names: Sequence[str], canonical: str) -> list[str]:
    if len(names) > MAX_ALIASES:
        raise Conflict("invalid_class_settings")
    out: list[str] = []
    for raw in names:
        name = clean_text(raw, 101)
        key = class_key(name)
        if not name or len(name) > 100 or key == canonical:
            raise Conflict("invalid_class_settings")
        if all(class_key(n) != key for n in out):
            out.append(name)
    return out


def _clean_settings(settings: ClassSettings, canonical: str) -> dict[str, Any]:
    grace = settings.grace_school_days
    if grace is not None and (isinstance(grace, bool) or not 0 <= grace <= 10):
        raise Conflict("invalid_class_settings")
    name = clean_text(settings.teacher_name, 121) if settings.teacher_name is not None else None
    email = clean_text(settings.teacher_email, 201) if settings.teacher_email is not None else None
    if (name is not None and len(name) > 120) or (email is not None and not (
            len(email) <= 200 and _EMAIL.match(email))):
        raise Conflict("invalid_class_settings")
    return {"grace": grace, "paper_only": bool(settings.paper_only), "teacher_name": name or None,
            "teacher_email": email, "aliases": _clean_aliases(settings.aliases, canonical)}


def _alias_keys(raw: str | None) -> set[str]:
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return set()
    return {class_key(n) for n in data if isinstance(n, str)} if isinstance(data, list) else set()


def set_class(conn: sqlite3.Connection, kid: str, class_name: str, settings: ClassSettings, actor: Actor, *,
              now: datetime, no_school_rows: Rows) -> None:
    who = who_from(actor, parent_only=True)
    canonical = class_key(clean_text(class_name, 120))
    if not canonical:
        raise Conflict("invalid_class_settings")
    clean = _clean_settings(settings, canonical)
    with write_txn(conn):
        if not repo.kid_has_assignments(conn, kid):
            raise NotFound("kid")
        before = _alias_keys(repo.aliases_json(conn, kid, canonical))
        aliases = clean.pop("aliases")
        repo.put_class_settings(conn, kid, canonical, aliases=json.dumps(aliases) if aliases else None, **clean)
        if {class_key(n) for n in aliases} != before:
            remerge_in_txn(conn, kid, who, now)
        recompute(conn, kid, now, no_school_rows)
    logger.info("recon set_class: %d aliases stored", len(aliases))
