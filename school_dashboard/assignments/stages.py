"""Stage transitions: taps, scrape registration, portal evidence."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime
from typing import Sequence

from .model import AssignmentState, LinkState

MISSES_TO_RECEIVE = 3
MIN_DAYS_CLEARED = 2
_FIRST_NUMBER = re.compile(r"-?(?:\d+(?:\.\d+)?|\.\d+)")
_LETTER_GRADE = re.compile(r"^(?:[a-d][+-]?|p|pass)$")
_RECEIVED_STATUSES = ("submitted", "graded")
_TAP_STAGE = {"done": 2, "turned_in": 3}
_PERSISTENT_EVIDENCE = ("migration", "teacher_confirmed")


class StageError(ValueError):
    """A stage change that the rules forbid."""


def _tap_task(state: AssignmentState, action: str, now: datetime) -> AssignmentState:
    closed = state.closed_at is not None
    if action == "done" and not closed:
        return replace(state, stage=2, stage_since=now, closed_at=now)
    if action == "undo" and closed:
        return replace(state, stage=1, stage_since=now, closed_at=None)
    raise StageError(f"action {action!r} is not valid for this task")


def apply_tap(state: AssignmentState, action: str, setter: str, now: datetime) -> AssignmentState:
    if setter not in ("parent", "kid"):
        raise StageError(f"setter {setter!r} may not tap")
    if state.kind == "practice":
        raise StageError("practice records follow the portal and cannot be tapped")
    if state.kind == "task":
        return _tap_task(state, action, now)
    if state.stage == 4:
        if action == "undo" and setter == "parent" and state.received_evidence == "teacher_confirmed":
            return replace(state, stage=3, stage_since=now, received_evidence=None, closed_at=None)
        raise StageError("received records cannot be changed by taps")
    if action in _TAP_STAGE:
        target = _TAP_STAGE[action]
    elif action == "undo":
        if state.stage == 1:
            raise StageError("nothing to undo at stage 1")
        target = 1
    else:
        raise StageError(f"unknown action {action!r}")
    return replace(state, stage=target, stage_since=now)


def confirm_by_teacher(state: AssignmentState, setter: str, now: datetime) -> AssignmentState:
    """A parent records that the teacher confirmed receipt: the only manual way to stage 4."""
    if setter != "parent":
        raise StageError("only a parent may record a teacher confirmation")
    if state.kind != "assignment" or state.stage not in (2, 3):
        raise StageError("only a done or turned-in assignment can be confirmed")
    return replace(state, stage=4, stage_since=now, received_evidence="teacher_confirmed", closed_at=now)


def register_scrape(link: LinkState, *, seen: bool, trusted: bool, file_hash: str, today: date,
                    new_status: str | None = None, new_score: str | None = None) -> LinkState:
    if not trusted:
        return link
    if seen:
        return replace(
            link, live=True, trusted_misses=0, absent_since=None, last_miss_hash=None,
            norm_status=new_status if new_status is not None else link.norm_status,
            score=new_score if new_score is not None else link.score)
    if link.via == "screenshot" or link.last_miss_hash == file_hash:
        return link
    return replace(link, live=False, trusted_misses=link.trusted_misses + 1,
                   absent_since=link.absent_since or today, last_miss_hash=file_hash)


def mark_absent_uncounted(link: LinkState, today: date) -> LinkState:
    """Not in this scrape, but the source only shows a window: mark not live, never count a miss."""
    if not link.live:
        return link
    return replace(link, live=False, absent_since=link.absent_since or today)


def _real_score(score: str | None) -> bool:
    """Score text that shows work was received: a leading number above 0, a letter grade A-D, or pass."""
    if score is None:
        return False
    text = score.strip().casefold()
    if _LETTER_GRADE.match(text):
        return True
    match = _FIRST_NUMBER.search(text.split("/", 1)[0])
    return match is not None and float(match.group()) > 0


def _scored(link: LinkState) -> bool:
    """A real score only counts on a link the portal shows as submitted or graded."""
    return link.norm_status in _RECEIVED_STATUSES and _real_score(link.score)


def _received_links(links: Sequence[LinkState]) -> list[LinkState]:
    """Live links from a scrape or manual entry; screenshots never count toward Received."""
    return [ln for ln in links if ln.live and ln.via != "screenshot"]


def _received_by_portal(links: Sequence[LinkState]) -> bool:
    """Owner rule: a real score, or two different sources each submitted/graded."""
    counted = _received_links(links)
    if any(_scored(ln) for ln in counted):
        return True
    return len({ln.source for ln in counted if ln.norm_status in ("submitted", "graded")}) >= 2


def portal_decision(state: AssignmentState, links: Sequence[LinkState],
                    today: date) -> tuple[int, str | None]:
    """(stage, received_evidence) after weighing portal evidence (plan 10.2, owner decision S4a-1)."""
    live = [ln for ln in links if ln.live]
    if any(ln.norm_status == "missing" for ln in live):
        if state.stage == 4:
            return 1, None
        return state.stage, state.received_evidence
    if state.stage == 4:
        return 4, state.received_evidence
    if _received_by_portal(links):
        return 4, "portal"
    if any(ln.norm_status == "submitted" for ln in live) or any(
            ln.norm_status == "graded" for ln in _received_links(links)):
        return max(state.stage, 3), state.received_evidence
    return state.stage, state.received_evidence


def evidence_sources(links: Sequence[LinkState], today: date) -> tuple[str, ...]:
    """Source names of the links that give portal evidence for stage 4; never titles."""
    return tuple(sorted({ln.source for ln in _received_links(links)
                         if ln.norm_status in _RECEIVED_STATUSES}))
