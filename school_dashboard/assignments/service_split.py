"""Manual split: a link leaves a record; a link that arrived by a manual merge restores the merged-away record."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Mapping, Sequence

from . import repo, repo_history
from .adapters.base import clean_text, parse_due
from .ingest_kid import parse_aliases
from .model import AssignmentState
from .normalize import class_key, title_key
from .service_merge import events, load_kid, rederive, stage_event
from .service_types import Actor, Conflict, NotFound, Who, load_open, recompute, who_from, write_txn

Rows = Sequence[Mapping[str, Any]]


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


def _merged_away_owner(conn: sqlite3.Connection, aid: int, link_id: int) -> int | None:
    """The record a manual merge folded into `aid` that owned `link_id`, if any."""
    return next((src for src in repo_history.merged_sources(conn, aid)
                 if link_id in repo_history.merge_link_ids(conn, src)), None)


def _settle(conn: sqlite3.Connection, target: int, other: int, link_id: int, floor: int, who: Who,
            now: datetime) -> None:
    current = repo.get_assignment(conn, target)
    if current is None:
        raise NotFound("assignment")
    updated = rederive(current, repo.link_records(conn, [target])[target], now, floor)
    repo.update_assignment(conn, updated, now=now)
    events(conn, target, who, now, "split", f"manual with={other} link={link_id}")
    stage_event(conn, current, updated, who, now, "split")


def split_in_txn(conn: sqlite3.Connection, aid: int, link_id: int, who: Who, now: datetime) -> int:
    state = load_open(conn, aid)
    links = repo.link_records(conn, [aid])[aid]
    if all(ln.id != link_id for ln in links):
        raise NotFound("link")
    if len(links) < 2:
        raise Conflict("last_link")
    restored = _merged_away_owner(conn, aid, link_id)
    if restored is None:
        new_id, new_floor = _split_new(conn, state, link_id, now), 1
        old_floor = repo_history.latest_manual_stage(conn, aid)
    else:
        owner = repo.get_assignment(conn, restored)
        if owner is None:
            raise NotFound("assignment")
        repo_history.reopen_merged(conn, restored, now)
        new_id = restored
        new_floor = max(repo_history.latest_manual_stage(conn, restored), owner.stage if owner.stage < 4 else 1)
        old_floor = repo_history.latest_manual_stage(conn, aid, ignore_details=("merge",))
    repo.move_link(conn, link_id, new_id)
    _settle(conn, aid, new_id, link_id, old_floor, who, now)
    _settle(conn, new_id, aid, link_id, new_floor, who, now)
    return new_id


def split(conn: sqlite3.Connection, assignment_id: int, link_id: int, actor: Actor, *, now: datetime,
          no_school_rows: Rows) -> int:
    who = who_from(actor, parent_only=True)
    with write_txn(conn):
        new_id = split_in_txn(conn, assignment_id, link_id, who, now)
        recompute(conn, load_kid(conn, assignment_id), now, no_school_rows)
    return new_id
