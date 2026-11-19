"""Manual merge, split and alias-driven re-merge (each public call is one transaction)."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping, Sequence

from . import merge as match_rules
from . import repo, schooldays, stages
from .adapters.base import clean_text, parse_due
from .ingest_kid import parse_aliases
from .model import AssignmentState, LinkRecord
from .normalize import class_key, title_key
from .service_types import Actor, Conflict, NotFound, Who, load_open, recompute, who_from, write_txn

logger = logging.getLogger(__name__)
_PERSISTENT = ("migration", "teacher_confirmed")
Rows = Sequence[Mapping[str, Any]]


def _rederive(state: AssignmentState, links: Sequence[LinkRecord], now: datetime) -> AssignmentState:
    """Stage and portal status from the links; a portal-received stage 4 is re-earned, not kept."""
    states = [ln.state for ln in links]
    base = state
    if state.stage == 4 and state.received_evidence not in _PERSISTENT:
        base = replace(state, stage=1, received_evidence=None)
    status, conflict = match_rules.combine_portal_status(states)
    base = replace(base, portal_status=status, portal_conflict=conflict)
    stage, evidence = stages.portal_decision(base, states, schooldays.school_today(now))
    new = replace(base, stage=stage, received_evidence=evidence)
    if stage != state.stage:
        new = replace(new, stage_since=now)
    return replace(new, closed_at=(state.closed_at or now) if stage == 4 else None)


def _merge_base(into: AssignmentState, src: AssignmentState, now: datetime) -> AssignmentState:
    kept = next((s for s in (into, src) if s.stage == 4 and s.received_evidence in _PERSISTENT), None)
    if kept is not None:
        return replace(into, stage=4, stage_since=kept.stage_since, received_evidence=kept.received_evidence,
                       closed_at=kept.closed_at or now)
    manual = [s for s in (into, src) if s.stage < 4]
    stage = max((s.stage for s in manual), default=1)
    since = next((s.stage_since for s in manual if s.stage == stage), into.stage_since)
    return replace(into, stage=stage, stage_since=since, received_evidence=None, closed_at=None)


def _events(conn: sqlite3.Connection, aid: int, who: Who, now: datetime, kind: str, detail: str = "manual") -> None:
    repo.append_event(conn, aid, at=now, setter=who.setter, actor=who.actor, kind=kind, detail=detail)


def _stage_event(conn: sqlite3.Connection, old: AssignmentState, new: AssignmentState, who: Who, now: datetime,
                 detail: str) -> None:
    if old.stage != new.stage and new.id is not None:
        repo.append_event(conn, new.id, at=now, setter=who.setter, actor=who.actor, kind="stage",
                          from_value=str(old.stage), to_value=str(new.stage), detail=detail)


def merge_in_txn(conn: sqlite3.Connection, src_id: int, into_id: int, who: Who, now: datetime) -> None:
    if src_id == into_id:
        raise Conflict("same_record")
    src, into = load_open(conn, src_id), load_open(conn, into_id)
    if src.kid != into.kid:
        raise Conflict("different_kid")
    if src.kind != "assignment" or into.kind != "assignment":
        raise Conflict("kind_mismatch")
    repo.move_links(conn, src_id, into_id)
    merged = _rederive(_merge_base(into, src, now), repo.link_records(conn, [into_id])[into_id], now)
    repo.update_assignment(conn, merged, now=now)
    repo.mark_merged(conn, src_id, into_id, now)
    _events(conn, into_id, who, now, "merge")
    _events(conn, src_id, who, now, "merge")
    _stage_event(conn, into, merged, who, now, "merge")


def merge(conn: sqlite3.Connection, source_id: int, into_id: int, actor: Actor, *, now: datetime,
          no_school_rows: Rows) -> int:
    who = who_from(actor, parent_only=True)
    with write_txn(conn):
        merge_in_txn(conn, source_id, into_id, who, now)
        recompute(conn, load_kid(conn, into_id), now, no_school_rows)
    return into_id


def load_kid(conn: sqlite3.Connection, assignment_id: int) -> str:
    state = repo.get_assignment(conn, assignment_id)
    if state is None:
        raise NotFound("assignment")
    return state.kid


def _raw_fields(raw_json: str | None, state: AssignmentState) -> tuple[str, str | None, Any]:
    try:
        raw = json.loads(raw_json or "{}")
    except ValueError:
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    due, _ = parse_due(raw.get("due_date") or raw.get("due"))
    return (clean_text(raw.get("title"), 300) or state.title,
            clean_text(raw.get("course") or raw.get("class_name"), 120) or state.class_name,
            due or state.due_date)


def _split_new(conn: sqlite3.Connection, state: AssignmentState, link_id: int, now: datetime) -> int:
    title, cls, due = _raw_fields(repo.link_raw_json(conn, link_id), state)
    aliases = parse_aliases(repo.class_settings_rows(conn, state.kid))
    fresh = AssignmentState(id=None, kid=state.kid, class_name=cls, class_key=class_key(cls, aliases), title=title,
                            title_key=title_key(title), due_date=due, kind="assignment", stage=1, stage_since=now)
    return repo.insert_assignment(conn, fresh, now=now)


def split_in_txn(conn: sqlite3.Connection, aid: int, link_id: int, who: Who, now: datetime) -> int:
    state = load_open(conn, aid)
    links = repo.link_records(conn, [aid])[aid]
    if all(ln.id != link_id for ln in links):
        raise NotFound("link")
    if len(links) < 2:
        raise Conflict("last_link")
    new_id = _split_new(conn, state, link_id, now)
    repo.move_link(conn, link_id, new_id)
    for target in (state.id, new_id):
        assert target is not None
        current = repo.get_assignment(conn, target)
        assert current is not None
        updated = _rederive(current, repo.link_records(conn, [target])[target], now)
        repo.update_assignment(conn, updated, now=now)
        _events(conn, target, who, now, "split")
        _stage_event(conn, current, updated, who, now, "split")
    return new_id


def split(conn: sqlite3.Connection, assignment_id: int, link_id: int, actor: Actor, *, now: datetime,
          no_school_rows: Rows) -> int:
    who = who_from(actor, parent_only=True)
    with write_txn(conn):
        new_id = split_in_txn(conn, assignment_id, link_id, who, now)
        recompute(conn, load_kid(conn, assignment_id), now, no_school_rows)
    return new_id


def _rekey(conn: sqlite3.Connection, kid: str, who: Who, now: datetime) -> tuple[list[AssignmentState], bool]:
    aliases = parse_aliases(repo.class_settings_rows(conn, kid))
    out, changed = [], False
    for state in repo.list_assignments(conn, kid, include_closed=True):
        key = class_key(state.class_name, aliases)
        if key != state.class_key and state.id is not None:
            aid = state.id
            state = replace(state, class_key=key)
            repo.update_assignment(conn, state, now=now)
            repo.append_event(conn, aid, at=now, setter=who.setter, actor=who.actor, kind="edit", detail="class")
            changed = True
        out.append(state)
    return out, changed


def _sources(conn: sqlite3.Connection, aid: int) -> frozenset[str]:
    return frozenset(ln.state.source for ln in repo.link_records(conn, [aid])[aid])


def remerge_in_txn(conn: sqlite3.Connection, kid: str, who: Who, now: datetime) -> tuple[tuple[int, int], ...]:
    states, _ = _rekey(conn, kid, who, now)
    groups: dict[tuple[str, str, Any], list[int]] = defaultdict(list)
    for s in states:
        if s.kind == "assignment" and s.class_key and s.due_date is not None and s.id is not None:
            groups[(s.class_key, s.title_key, s.due_date)].append(s.id)
    done: list[tuple[int, int]] = []
    ambiguous = 0
    for ids in groups.values():
        if len(ids) > 2:
            ambiguous += 1
        elif len(ids) == 2:
            lo, hi = sorted(ids)
            a, b = _sources(conn, lo), _sources(conn, hi)
            if a and b and not a & b:
                merge_in_txn(conn, hi, lo, who, now)
                done.append((hi, lo))
    logger.info("recon remerge: %d merged, %d ambiguous groups left alone", len(done), ambiguous)
    return tuple(done)


def remerge_kid(conn: sqlite3.Connection, kid: str, actor: Actor | None, *, now: datetime,
                no_school_rows: Rows) -> tuple[tuple[int, int], ...]:
    who = who_from(actor, parent_only=True)
    with write_txn(conn):
        pairs = remerge_in_txn(conn, kid, who, now)
        recompute(conn, kid, now, no_school_rows)
    return pairs
