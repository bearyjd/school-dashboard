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
    warnings: tuple[str, ...] = ()


def _when(value: Any, now: datetime, bad: Counter, kind: str) -> datetime:
    """Parse a legacy timestamp; naive values take the zone of `now`; unparsable ones fall back to `now`
    (and are counted in `bad[kind]` when they were non-empty)."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        if value:
            bad[kind] += 1
        return now
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=now.tzinfo)


def _due(value: Any, bad: Counter) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        bad["due"] += 1
        return None


def _bad_warnings(bad: Counter) -> tuple[str, ...]:
    texts = {"created_at": "migration time used", "completed_at": "migration time used",
             "due": "left undated"}
    return tuple(f"{bad[k]} rows had an unreadable {k}; {t}" for k, t in texts.items() if bad[k])


def _state(kid: str, title: str, due: date | None, kind: str, created: datetime,
           closed: datetime | None) -> AssignmentState:
    done = closed is not None
    return AssignmentState(
        id=None, kid=kid, class_name=None, class_key="", title=title, title_key=title_key(title),
        due_date=due, kind=kind, stage=4 if done else 1, stage_since=closed if done else created,
        received_evidence="migration" if done else None, closed_at=closed)


def _record(state: AssignmentState, row: Mapping[str, Any], source: str, key: str, notes: str | None,
            item_id: int | None, action_id: str | None) -> PlannedRecord:
    link = PlannedLink(source, key, json.dumps(dict(row), sort_keys=True, default=str))
    return PlannedRecord(state, notes, item_id, action_id, (link,))


def _plan_items(items: Sequence[Mapping[str, Any]], now: datetime, bad: Counter,
                resolve_kid: Callable[[str], str]) -> list[PlannedRecord]:
    out = []
    for it in items:
        created = _when(it.get("created_at"), now, bad, "created_at")
        closed = None
        if it.get("completed"):
            closed = _when(it["completed_at"], now, bad, "completed_at") if it.get("completed_at") else created
        kind = "assignment" if it.get("type") == "assignment" else "task"
        raw_kid = str(it.get("child") or "")
        state = _state(resolve_kid(raw_kid) or raw_kid.strip(), str(it.get("title") or ""),
                       _due(it.get("due_date"), bad), kind, created, closed)
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
                  bad: Counter, resolve_kid: Callable[[str], str]) -> list[Mapping[str, Any]]:
    merged: list[Mapping[str, Any]] = []
    for act in actions:
        created = _when(act.get("created"), now, bad, "created_at")
        closed = None
        if act.get("status") == "completed":
            closed = _when(act["completed_at"], now, bad, "completed_at") if act.get("completed_at") else created
        kind = "assignment" if act.get("type") == "assignment" else "task"
        raw_kid = str(act.get("child") or "")
        state = _state(resolve_kid(raw_kid) or raw_kid.strip(), str(act.get("summary") or ""),
                       _due(act.get("due"), bad), kind, created, closed)
        key = f"legacy-action:{act['id']}"
        new = _record(state, act, str(act.get("source") or "manual"), key, None, None, str(act["id"]))
        idx = _merge_target(records, state)
        if idx is None:
            records.append(new)
            continue
        target = records[idx]
        merged.append({"item_key": target.links[0].external_key, "action_key": key})
        # the merged record keeps the more-closed state of the two sides
        closed_state = new.state if target.state.stage != 4 and new.state.stage == 4 else target.state
        records[idx] = replace(target, state=closed_state, legacy_action_id=str(act["id"]),
                               links=target.links + new.links)
    return merged


def plan_migration(items: Sequence[Mapping[str, Any]], action_items: Sequence[Mapping[str, Any]], *,
                   now: datetime, resolve_kid: Callable[[str], str]) -> MigrationPlan:
    bad: Counter = Counter()
    records = _plan_items(items, now, bad, resolve_kid)
    merged = _plan_actions(records, action_items, now, bad, resolve_kid)
    per_kid = Counter(r.state.kid for r in records)
    return MigrationPlan(tuple(records), tuple(merged), dict(sorted(per_kid.items())),
                         len(items) + len(action_items), _bad_warnings(bad))
