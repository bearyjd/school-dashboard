"""Ingest steps that follow record application: absence, portal status/stage, stored flags.

All run inside the caller's per-kid transaction. Inputs are never mutated; `counts` is the one
working tally the caller owns.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Mapping

from . import flags, merge, repo, stages
from .model import AssignmentState, LinkState

ABSENCE_SOURCES = ("powerschool", "schoology")
DEFAULT_GRACE = 2
PUSH_DEBOUNCE = timedelta(hours=6)  # no second push for the same (assignment, flag) inside this window


@dataclass(frozen=True)
class KidContext:
    kid: str
    now: datetime
    today: date
    baseline: bool
    aliases: Mapping[str, str]
    grace: Mapping[str, int]  # class_key -> grace school days
    no_school: frozenset[date]
    file_hashes: Mapping[str, str]


def _mass_change(unseen: int, live: int) -> bool:
    return (unseen >= 3 and unseen * 2 > live) or (live >= 2 and unseen == live)


def _absent_link(link: LinkState, source: str, ctx: KidContext) -> LinkState:
    if source == "powerschool":
        return stages.register_scrape(link, seen=False, trusted=True,
                                      file_hash=ctx.file_hashes.get(source, ""), today=ctx.today)
    if link.via != "scrape":
        return link
    return stages.mark_absent_uncounted(link, ctx.today)


def apply_absence(conn: sqlite3.Connection, ctx: KidContext, seen: frozenset[tuple[str, str]], trusted_sources: frozenset[str],
                  counts: Counter[str], touched: set[int]) -> list[str]:
    """Absence for links whose source produced a trusted entry; returns warnings."""
    warnings: list[str] = []
    for source in ABSENCE_SOURCES:
        if source not in trusted_sources:
            continue
        rows = repo.kid_links(conn, ctx.kid, source)
        live = [r for r in rows if r[1].live]
        unseen = [r for r in rows if (source, r[1].external_key) not in seen]
        if _mass_change(sum(1 for r in unseen if r[1].live), len(live)):
            warnings.append(f"{source}: mass change guard held back absence for {len(unseen)} links")
            continue
        for aid, link in unseen:
            updated = _absent_link(link, source, ctx)
            if updated != link:
                repo.update_link_absence(conn, aid, updated)
                counts["absent"] += 1
                touched.add(aid)
    return warnings


def _decide(state: AssignmentState, links: list[LinkState], ctx: KidContext) -> AssignmentState:
    status, conflict = merge.combine_portal_status(links)
    base = replace(state, portal_status=status, portal_conflict=conflict)
    stage, evidence = stages.portal_decision(base, links, ctx.today)
    new = replace(base, stage=stage, received_evidence=evidence)
    if stage != state.stage:
        new = replace(new, stage_since=ctx.now)
    if stage == 4 and state.closed_at is None:
        return replace(new, closed_at=ctx.now)
    if stage != 4 and state.closed_at is not None:
        return replace(new, closed_at=None)
    return new


def _log_changes(conn: sqlite3.Connection, ctx: KidContext, old: AssignmentState, new: AssignmentState,
                 links: list[LinkState]) -> None:
    assert new.id is not None
    if old.portal_status != new.portal_status:
        repo.append_event(conn, new.id, at=ctx.now, setter="portal", kind="portal_status",
                          from_value=old.portal_status, to_value=new.portal_status)
    if old.stage != new.stage:
        repo.append_event(conn, new.id, at=ctx.now, setter="portal", kind="stage",
                          from_value=str(old.stage), to_value=str(new.stage),
                          detail=(",".join(stages.evidence_sources(links, ctx.today)) if new.stage == 4 else "")
                          or new.received_evidence)


def refresh_status(conn: sqlite3.Connection, ctx: KidContext, working: Mapping[int, AssignmentState], touched: set[int],
                   counts: Counter[str]) -> None:
    """Portal status and stage for every open or touched assignment; one event per change."""
    for aid, state in working.items():
        if state.stage >= 4 and aid not in touched:
            continue
        links = repo.links_for(conn, aid)
        new = _decide(state, links, ctx)
        if new == state:
            continue
        repo.update_assignment(conn, new, now=ctx.now)
        _log_changes(conn, ctx, state, new, links)
        counts["stage_changes"] += int(new.stage != state.stage)


def _wants_push(flag: str | None, state: AssignmentState) -> bool:
    """Plan 10.5: disputed, and missing only when the portal itself says missing."""
    return flag == "disputed" or (flag == "missing" and state.portal_status == "missing")


def _portal_missing_now(conn: sqlite3.Connection, aid: int, now: datetime) -> bool:
    """True when this run's portal evidence just turned the assignment `missing` (an event stamped `now`)."""
    return conn.execute(
        "SELECT 1 FROM assignment_events WHERE assignment_id = ? AND kind = 'portal_status'"
        " AND to_value = 'missing' AND at = ?", (aid, now.isoformat())).fetchone() is not None


def enqueue_debounced(conn: sqlite3.Connection, ctx: KidContext, aid: int, flag: str,
                      counts: Counter[str]) -> None:
    """Queue a push unless one for the same (assignment, flag) was queued in the last PUSH_DEBOUNCE."""
    if repo.recent_push_exists(conn, aid, flag, ctx.now - PUSH_DEBOUNCE):
        # visible in the report; never starts a window; counted only when the row was really inserted
        if repo.record_debounced_push(conn, aid, flag, ctx.now):
            counts["pushes_debounced"] += 1
        return
    repo.enqueue_push(conn, aid, flag, ctx.now, "baseline" if ctx.baseline else "transition")
    counts["pushes_enqueued"] += 1


def apply_flags(conn: sqlite3.Connection, ctx: KidContext, counts: Counter[str]) -> None:
    """Store flag transitions (injected now; unverified is a read-time overlay, not applied here)."""
    stored = repo.stored_flags(conn, ctx.kid)
    for state in repo.list_assignments(conn, ctx.kid, include_closed=True):
        if state.id is None or (state.stage >= 4 and stored.get(state.id) is None):
            continue
        result = flags.compute_flag(state, repo.links_for(conn, state.id), now=ctx.now,
                                    no_school=ctx.no_school,
                                    grace_days=ctx.grace.get(state.class_key, DEFAULT_GRACE))
        old = stored.get(state.id)
        if result.flag == old:
            if old == "missing" and state.portal_status == "missing" and _portal_missing_now(conn, state.id, ctx.now):
                enqueue_debounced(conn, ctx, state.id, "missing", counts)  # portal evidence arrived on a date-missing record
            continue
        repo.set_flag(conn, state.id, result.flag, ctx.now)
        repo.append_event(conn, state.id, at=ctx.now, setter="system", kind="flag",
                          from_value=old, to_value=result.flag)
        counts["flag_changes"] += 1
        if result.flag and _wants_push(result.flag, state):
            enqueue_debounced(conn, ctx, state.id, result.flag, counts)
