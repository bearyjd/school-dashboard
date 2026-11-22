"""Manual merge and alias-driven re-merge (each public call is one transaction)."""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping, Sequence

from . import merge as match_rules
from . import repo, repo_history, schooldays, stages
from .ingest_kid import parse_aliases
from .normalize import class_key
from .model import AssignmentState, LinkRecord
from .service_types import (Actor, Conflict, NotFound, RemergeResult, Who, load_open, recompute, who_from,
                            write_txn)

logger = logging.getLogger(__name__)
_PERSISTENT = ("migration", "teacher_confirmed")
MAX_DETAIL = 500
Rows = Sequence[Mapping[str, Any]]


def persistently_closed(state: AssignmentState) -> bool:
    return state.stage == 4 and state.received_evidence in _PERSISTENT


def rederive(state: AssignmentState, links: Sequence[LinkRecord], now: datetime, floor: int) -> AssignmentState:
    """Stage = max(portal-derived, `floor`, the manual stage); a portal-received stage 4 is re-earned, not kept."""
    states = [ln.state for ln in links]
    base = state if persistently_closed(state) else replace(state, stage=floor, received_evidence=None)
    status, conflict = match_rules.combine_portal_status(states)
    base = replace(base, portal_status=status, portal_conflict=conflict)
    stage, evidence = stages.portal_decision(base, states, schooldays.school_today(now))
    new = replace(base, stage=stage, received_evidence=evidence)
    if stage != state.stage:
        new = replace(new, stage_since=now)
    return replace(new, closed_at=(state.closed_at or now) if stage == 4 else None)


def side_floor(conn: sqlite3.Connection, state: AssignmentState) -> int:
    """The stage a record holds on its own: its stored stage, or its manual history when it is portal-received."""
    return state.stage if state.stage < 4 else repo_history.latest_manual_stage(conn, state.id or 0)


def events(conn: sqlite3.Connection, aid: int, who: Who, now: datetime, kind: str, detail: str = "manual") -> None:
    repo.append_event(conn, aid, at=now, setter=who.setter, actor=who.actor, kind=kind, detail=detail)


def stage_event(conn: sqlite3.Connection, old: AssignmentState, new: AssignmentState, who: Who, now: datetime,
                detail: str) -> None:
    if old.stage != new.stage and new.id is not None:
        repo.append_event(conn, new.id, at=now, setter=who.setter, actor=who.actor, kind="stage",
                          from_value=str(old.stage), to_value=str(new.stage), detail=detail)


def merge_refusal(conn: sqlite3.Connection, src: AssignmentState, into: AssignmentState, force: bool) -> str | None:
    """Why this pair may not merge (a Conflict code), or None."""
    if src.id == into.id:
        return "same_record"
    if src.kid != into.kid:
        return "different_kid"
    if src.kind != "assignment" or into.kind != "assignment":
        return "kind_mismatch"
    if src.dismissed_at or into.dismissed_at or persistently_closed(src) or persistently_closed(into):
        return "closed_record"
    if not force and (not src.class_key or src.class_key != into.class_key):
        return "class_mismatch"
    if len(_detail(conn, src.id or 0, force)) > MAX_DETAIL:
        return "too_many_links"
    return None


def _detail(conn: sqlite3.Connection, src_id: int, force: bool) -> str:
    ids = ",".join(str(ln.id) for ln in repo.link_records(conn, [src_id])[src_id])
    return " ".join(p for p in ("manual", "forced" if force else "", f"links={ids}" if ids else "") if p)


def merge_in_txn(conn: sqlite3.Connection, src_id: int, into_id: int, who: Who, now: datetime, *,
                 force: bool = False) -> None:
    src, into = load_open(conn, src_id), load_open(conn, into_id)
    code = merge_refusal(conn, src, into, force)
    if code:
        raise Conflict(code)
    detail = _detail(conn, src_id, force)
    floor = max(side_floor(conn, into), side_floor(conn, src))
    since = next((s.stage_since for s in (into, src) if s.stage == floor), into.stage_since)
    repo.move_links(conn, src_id, into_id)
    base = replace(into, stage=floor, stage_since=since, received_evidence=None, closed_at=None)
    merged = rederive(base, repo.link_records(conn, [into_id])[into_id], now, floor)
    repo.update_assignment(conn, merged, now=now)
    repo.mark_merged(conn, src_id, into_id, now)
    events(conn, into_id, who, now, "merge", detail)
    events(conn, src_id, who, now, "merge", detail)
    stage_event(conn, into, merged, who, now, "merge")


def merge(conn: sqlite3.Connection, source_id: int, into_id: int, actor: Actor, *, now: datetime,
          no_school_rows: Rows, force: bool = False) -> int:
    who = who_from(actor, parent_only=True)
    with write_txn(conn):
        merge_in_txn(conn, source_id, into_id, who, now, force=force)
        recompute(conn, load_kid(conn, into_id), now, no_school_rows)
    return into_id


def load_kid(conn: sqlite3.Connection, assignment_id: int) -> str:
    state = repo.get_assignment(conn, assignment_id)
    if state is None:
        raise NotFound("assignment")
    return state.kid


def _rekey(conn: sqlite3.Connection, kid: str, who: Who, now: datetime) -> list[AssignmentState]:
    aliases = parse_aliases(repo.class_settings_rows(conn, kid))
    out = []
    for state in repo.list_assignments(conn, kid, include_closed=True):
        key = class_key(state.class_name, aliases)
        if key != state.class_key and state.id is not None:
            aid = state.id
            state = replace(state, class_key=key)
            repo.update_assignment(conn, state, now=now)
            repo.append_event(conn, aid, at=now, setter=who.setter, actor=who.actor, kind="edit", detail="class")
        out.append(state)
    return out


def _sources(conn: sqlite3.Connection, aid: int) -> frozenset[str]:
    return frozenset(ln.state.source for ln in repo.link_records(conn, [aid])[aid])


def _may_remerge(conn: sqlite3.Connection, lo: AssignmentState, hi: AssignmentState) -> bool:
    a, b = _sources(conn, lo.id or 0), _sources(conn, hi.id or 0)
    return bool(a and b and not a & b and not repo_history.split_partner_exists(conn, lo.id or 0, hi.id or 0)
                and merge_refusal(conn, hi, lo, False) is None)


def remerge_in_txn(conn: sqlite3.Connection, kid: str, who: Who, now: datetime) -> RemergeResult:
    groups: dict[tuple[str, str, Any], list[AssignmentState]] = defaultdict(list)
    for s in _rekey(conn, kid, who, now):
        if s.kind == "assignment" and s.class_key and s.due_date is not None and s.id is not None:
            groups[(s.class_key, s.title_key, s.due_date)].append(s)
    done: list[tuple[int, int]] = []
    ambiguous = 0
    for members in groups.values():
        if len(members) > 2:
            ambiguous += 1
        elif len(members) == 2:
            lo, hi = sorted(members, key=lambda m: m.id or 0)
            if _may_remerge(conn, lo, hi):
                merge_in_txn(conn, hi.id or 0, lo.id or 0, who, now)
                done.append((hi.id or 0, lo.id or 0))
    logger.info("recon remerge: %d merged, %d ambiguous groups left alone", len(done), ambiguous)
    return RemergeResult(tuple(done), ambiguous)


def remerge_kid(conn: sqlite3.Connection, kid: str, actor: Actor | None, *, now: datetime,
                no_school_rows: Rows) -> RemergeResult:
    who = who_from(actor, parent_only=True)
    with write_txn(conn):
        result = remerge_in_txn(conn, kid, who, now)
        recompute(conn, kid, now, no_school_rows)
    return result
