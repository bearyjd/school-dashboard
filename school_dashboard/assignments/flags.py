"""Flag engine: pure, computed on read with an injected now."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

from .model import BUCKETS, AssignmentState, FlagResult, LinkState
from .schooldays import add_school_days, next_school_day, school_today

_EXEMPT_KINDS = ("task", "practice")


def _info(state: AssignmentState, links: Sequence[LinkState]) -> tuple[str, ...]:
    out: list[str] = []
    if any(ln.live and ln.norm_status == "late" for ln in links):
        out.append("late")
    if state.portal_status == "cleared":
        out.append("cleared_unconfirmed")
    if state.stage == 3 and (state.paper_only or not links):
        out.append("unconfirmed_paper")
    if state.portal_conflict:
        out.append("conflict")
    return tuple(out)


def _flag(state: AssignmentState, now: datetime, no_school: frozenset[date],
          grace_days: int) -> str:
    today = school_today(now)
    stage = 1 if state.kind in _EXEMPT_KINDS else state.stage
    if stage == 3 and state.portal_status == "missing":
        return "disputed"
    if state.portal_status == "missing" or (
            stage == 1 and state.due_date is not None and state.due_date < today):
        return "missing"
    if stage in (2, 3):
        since = state.stage_since.astimezone(now.tzinfo).date()
        start = max(since, state.due_date) if state.due_date else since
        waiting = today <= add_school_days(start, grace_days, no_school)
        return "waiting_on_teacher" if waiting else "done_not_received"
    if state.due_date is not None and state.due_date <= next_school_day(today, no_school):
        return "do_tonight"
    return "upcoming"


def compute_flag(state: AssignmentState, links: Sequence[LinkState], *, now: datetime,
                 no_school: frozenset[date], grace_days: int = 2,
                 stale_sources: frozenset[str] = frozenset(), ingest_ok: bool = True) -> FlagResult:
    stale = {ln.source for ln in links if ln.source in stale_sources}
    if not ingest_ok:
        stale.add("recon_ingest")
    info = _info(state, links)
    unverified, unverified_sources = bool(stale), tuple(sorted(stale))
    if state.inactive:
        return FlagResult(flag=None, bucket=None, info=info, unverified=unverified,
                      unverified_sources=unverified_sources)
    flag = _flag(state, now, no_school, grace_days)
    if flag == "upcoming" and state.due_date is None and state.stage == 1:
        info = info + ("no_due_date",)
    return FlagResult(flag=flag, bucket=BUCKETS.get(flag), info=info, unverified=unverified,
                      unverified_sources=unverified_sources)


def bucket_counts(results: Iterable[FlagResult]) -> Mapping[str, int]:
    return dict(Counter(r.bucket for r in results if r.bucket))
