"""Pure planning step of the items -> assignments migration (no I/O)."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Callable, Mapping, Sequence

from school_dashboard.assignments.model import AssignmentState
from school_dashboard.assignments.normalize import title_key


@dataclass(frozen=True)
class PlannedLink:
    source: str
    external_key: str
    raw_json: str


@dataclass(frozen=True)
class PlannedRecord:
    state: AssignmentState
    notes: str | None
    legacy_item_id: int | None
    legacy_action_id: str | None
    links: tuple[PlannedLink, ...]


@dataclass(frozen=True)
class MigrationPlan:
    records: tuple[PlannedRecord, ...]
    merged: tuple[Mapping[str, Any], ...]
    per_kid: Mapping[str, int]
    rows_in: int


def _when(value: Any, now: datetime) -> datetime:
    """Parse a legacy timestamp; naive values take the zone of `now`; unparsable ones fall back to `now`."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return now
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=now.tzinfo)


def _due(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _state(kid: str, title: str, due: Any, kind: str, created: datetime,
           closed: datetime | None) -> AssignmentState:
    done = closed is not None
    return AssignmentState(
        id=None, kid=kid, class_name=None, class_key="", title=title, title_key=title_key(title),
        due_date=_due(due), kind=kind, stage=4 if done else 1, stage_since=closed if done else created,
        received_evidence="migration" if done else None, closed_at=closed)


def _record(state: AssignmentState, row: Mapping[str, Any], source: str, key: str, notes: str | None,
            item_id: int | None, action_id: str | None) -> PlannedRecord:
    link = PlannedLink(source, key, json.dumps(dict(row), sort_keys=True, default=str))
    return PlannedRecord(state, notes, item_id, action_id, (link,))


def _plan_items(items: Sequence[Mapping[str, Any]], now: datetime,
                resolve_kid: Callable[[str], str]) -> list[PlannedRecord]:
    out = []
    for it in items:
        created = _when(it.get("created_at"), now)
        closed = _when(it.get("completed_at") or it.get("created_at"), now) if it.get("completed") else None
        kind = "assignment" if it.get("type") == "assignment" else "task"
        raw_kid = str(it.get("child") or "")
        state = _state(resolve_kid(raw_kid) or raw_kid.strip(), str(it.get("title") or ""),
                       it.get("due_date"), kind, created, closed)
        out.append(_record(state, it, str(it.get("source") or "manual"), f"item:{it['id']}",
                           it.get("notes"), int(it["id"]), None))
    return out


def _merge_target(records: Sequence[PlannedRecord], state: AssignmentState) -> int | None:
    """Index of the single item-derived record with the same kid + title key + due date, else None."""
    if state.due_date is None:
        return None
    hits = [i for i, r in enumerate(records)
            if r.legacy_item_id is not None and r.legacy_action_id is None
            and (r.state.kid, r.state.title_key, r.state.due_date)
            == (state.kid, state.title_key, state.due_date)]
    return hits[0] if len(hits) == 1 else None


def _plan_actions(records: list[PlannedRecord], actions: Sequence[Mapping[str, Any]], now: datetime,
                  resolve_kid: Callable[[str], str]) -> list[Mapping[str, Any]]:
    merged: list[Mapping[str, Any]] = []
    for act in actions:
        created = _when(act.get("created"), now)
        done = act.get("status") == "completed"
        closed = _when(act.get("completed_at") or act.get("created"), now) if done else None
        kind = "assignment" if act.get("type") == "assignment" else "task"
        raw_kid = str(act.get("child") or "")
        state = _state(resolve_kid(raw_kid) or raw_kid.strip(), str(act.get("summary") or ""),
                       act.get("due"), kind, created, closed)
        key = f"legacy-action:{act['id']}"
        new = _record(state, act, str(act.get("source") or "manual"), key, None, None, str(act["id"]))
        idx = _merge_target(records, state)
        if idx is None:
            records.append(new)
            continue
        target = records[idx]
        merged.append({"item_key": target.links[0].external_key, "action_key": key})
        records[idx] = replace(target, legacy_action_id=str(act["id"]), links=target.links + new.links)
    return merged


def plan_migration(items: Sequence[Mapping[str, Any]], action_items: Sequence[Mapping[str, Any]], *,
                   now: datetime, resolve_kid: Callable[[str], str]) -> MigrationPlan:
    records = _plan_items(items, now, resolve_kid)
    merged = _plan_actions(records, action_items, now, resolve_kid)
    per_kid = Counter(r.state.kid for r in records)
    return MigrationPlan(tuple(records), tuple(merged), dict(sorted(per_kid.items())),
                         len(items) + len(action_items))
