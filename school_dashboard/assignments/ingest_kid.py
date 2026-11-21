"""One kid's ingest, run inside the caller's single repo.transaction (read, merge, stage, flag, outbox)."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Sequence

from . import merge, repo, schooldays, stages
from .ingest_status import KidContext, apply_absence, apply_flags, refresh_status
from .ingest_types import KidResult
from .model import AssignmentState, LinkState, SourceRecord
from .normalize import class_key, title_key

_KIND = {"ixl": "practice"}
MAX_RAW_JSON_BYTES = 8 * 1024


@dataclass
class _Working:
    """Mutable per-kid working set, local to one transaction (never shared, never an input)."""

    assignments: dict[int, AssignmentState]
    links: dict[tuple[str, str, str], int]
    touched: set[int] = field(default_factory=set)


def parse_aliases(rows: Sequence[tuple[str, int | None, str | None]]) -> dict[str, str]:
    """alias -> canonical class_key from class_settings.aliases (a JSON list of names); bad JSON is ignored."""
    out: dict[str, str] = {}
    for key, _, raw in rows:
        try:
            names = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            continue
        if isinstance(names, list):
            out.update({n: key for n in names if isinstance(n, str) and n.strip()})
    return out


def build_context(conn: sqlite3.Connection, kid: str, *, now: datetime, no_school_rows: Sequence[Mapping[str, Any]], baseline: bool,
                  file_hashes: Mapping[str, str]) -> KidContext:
    rows = repo.class_settings_rows(conn, kid)
    return KidContext(
        kid=kid, now=now, today=schooldays.school_today(now), baseline=baseline,
        aliases=parse_aliases(rows), grace={k: g for k, g, _ in rows if g is not None},
        no_school=schooldays.no_school_dates(no_school_rows, kid), file_hashes=file_hashes)


def _raw_json(rec: SourceRecord) -> str:
    raw = json.dumps(rec.raw, default=str, sort_keys=True)
    size = len(raw.encode("utf-8"))
    return raw if size <= MAX_RAW_JSON_BYTES else json.dumps({"truncated": True, "bytes": size})


def _link_for(conn: sqlite3.Connection, aid: int, rec: SourceRecord) -> LinkState:
    return next(ln for ln in repo.links_for(conn, aid)
                if ln.source == rec.source and ln.external_key == rec.external_key)


def _edits(state: AssignmentState, rec: SourceRecord, ctx: KidContext) -> AssignmentState:
    return replace(
        state, title=rec.title, title_key=title_key(rec.title), class_name=rec.class_name,
        class_key=class_key(rec.class_name, ctx.aliases), due_date=rec.due_date)


def _update_linked(conn: sqlite3.Connection, ctx: KidContext, w: _Working, rec: SourceRecord, aid: int, counts: Counter[str]) -> None:
    link = stages.register_scrape(_link_for(conn, aid, rec), seen=True, trusted=True,
                                  file_hash=ctx.file_hashes.get(rec.source, ""), today=ctx.today,
                                  new_status=rec.portal_status, new_score=rec.score)
    repo.upsert_link(conn, aid, ctx.kid, replace(link, url=rec.link or link.url), now=ctx.now,
                     raw_status=rec.raw_status, raw_json=_raw_json(rec))
    counts["linked"] += 1
    w.touched.add(aid)
    state = w.assignments[aid]
    changed = _edits(state, rec, ctx)
    fields = [n for n, differs in (("title", changed.title_key != state.title_key),
                                   ("class", changed.class_key != state.class_key),
                                   ("due", changed.due_date != state.due_date)) if differs]
    if fields and len(repo.links_for(conn, aid)) == 1:  # with several sources, none may overwrite the others
        repo.update_assignment(conn, changed, now=ctx.now)
        repo.append_event(conn, aid, at=ctx.now, setter="portal", kind="edit", source=rec.source,
                          detail=",".join(fields))
        w.assignments[aid] = changed


def _add_link(conn: sqlite3.Connection, ctx: KidContext, w: _Working, rec: SourceRecord, aid: int) -> None:
    link = LinkState(source=rec.source, external_key=rec.external_key, norm_status=rec.portal_status,
                     score=rec.score, url=rec.link, via=rec.via)
    repo.upsert_link(conn, aid, ctx.kid, link, now=ctx.now, raw_status=rec.raw_status, raw_json=_raw_json(rec))
    w.links[(rec.source, ctx.kid, rec.external_key)] = aid
    w.touched.add(aid)


def _attach(conn: sqlite3.Connection, ctx: KidContext, w: _Working, rec: SourceRecord, found: merge.MatchResult,
            counts: Counter[str]) -> None:
    assert found.assignment_id is not None
    _add_link(conn, ctx, w, rec, found.assignment_id)
    repo.append_event(conn, found.assignment_id, at=ctx.now, setter="portal", kind="merge",
                      source=rec.source, detail=found.kind)
    counts["merged"] += 1


def _create(conn: sqlite3.Connection, ctx: KidContext, w: _Working, rec: SourceRecord, found: merge.MatchResult,
            counts: Counter[str]) -> None:
    state = AssignmentState(
        id=None, kid=ctx.kid, class_name=rec.class_name, class_key=class_key(rec.class_name, ctx.aliases),
        title=rec.title, title_key=title_key(rec.title), due_date=rec.due_date,
        kind=_KIND.get(rec.source, "assignment"), stage=1, stage_since=ctx.now)
    aid = repo.insert_assignment(conn, state, now=ctx.now)
    w.assignments[aid] = replace(state, id=aid)
    _add_link(conn, ctx, w, rec, aid)
    repo.append_event(conn, aid, at=ctx.now, setter="portal", kind="stage", source=rec.source, to_value="1")
    counts["created"] += 1
    if found.kind == "ambiguous":
        ids = ",".join(str(i) for i in found.candidate_ids)
        repo.append_event(conn, aid, at=ctx.now, setter="portal", kind="merge", source=rec.source,
                          detail=f"possible_duplicate:{ids}")
        counts["ambiguous"] += 1


def _apply_record(conn: sqlite3.Connection, ctx: KidContext, w: _Working, rec: SourceRecord, counts: Counter[str]) -> None:
    found = merge.match(rec, list(w.assignments.values()), w.links, ctx.aliases)
    if found.kind == "link" and found.assignment_id is not None:
        _update_linked(conn, ctx, w, rec, found.assignment_id, counts)
    elif found.kind in ("key", "relaxed"):
        _attach(conn, ctx, w, rec, found, counts)
    else:
        _create(conn, ctx, w, rec, found, counts)


def _result(kid: str, counts: Counter[str]) -> KidResult:
    return KidResult(kid=kid, ok=True, created=counts["created"], linked=counts["linked"],
                     merged=counts["merged"], ambiguous=counts["ambiguous"], absent=counts["absent"],
                     stage_changes=counts["stage_changes"], flag_changes=counts["flag_changes"],
                     pushes_enqueued=counts["pushes_enqueued"],
                     pushes_debounced=counts["pushes_debounced"])


def run_kid(conn: sqlite3.Connection, kid: str, records: Sequence[SourceRecord], trusted_sources: frozenset[str], *, now: datetime,
            no_school_rows: Sequence[Mapping[str, Any]], baseline: bool,
            file_hashes: Mapping[str, str]) -> tuple[KidResult, tuple[str, ...]]:
    ctx = build_context(conn, kid, now=now, no_school_rows=no_school_rows, baseline=baseline,
                        file_hashes=file_hashes)
    w = _Working({a.id: a for a in repo.list_assignments(conn, kid, include_closed=True) if a.id is not None},
                 repo.link_index(conn, kid))
    counts: Counter[str] = Counter()
    for rec in records:
        _apply_record(conn, ctx, w, rec, counts)
    seen = frozenset((r.source, r.external_key) for r in records)
    warnings = apply_absence(conn, ctx, seen, trusted_sources, counts, w.touched)
    refresh_status(conn, ctx, w.assignments, w.touched, counts)
    apply_flags(conn, ctx, counts)
    return _result(kid, counts), tuple(warnings)


def recompute_kid(conn: sqlite3.Connection, kid: str, *, now: datetime, no_school_rows: Sequence[Mapping[str, Any]],
                  baseline: bool) -> KidResult:
    ctx = build_context(conn, kid, now=now, no_school_rows=no_school_rows, baseline=baseline, file_hashes={})
    counts: Counter[str] = Counter()
    apply_flags(conn, ctx, counts)
    return _result(kid, counts)
