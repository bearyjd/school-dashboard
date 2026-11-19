"""Stage transitions: taps, scrape registration, portal evidence."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Sequence

from .model import AssignmentState, LinkState

MISSES_TO_RECEIVE = 3
MIN_DAYS_CLEARED = 2
_PLACEHOLDER_SCORES = frozenset({"", "-", "--", "n/a", "missing"})
_TAP_STAGE = {"done": 2, "turned_in": 3}
_PERSISTENT_EVIDENCE = ("migration", "teacher_confirmed")


class StageError(ValueError):
    """A stage change that the rules forbid."""


def apply_tap(state: AssignmentState, action: str, setter: str, now: datetime) -> AssignmentState:
    if setter not in ("parent", "kid"):
        raise StageError(f"setter {setter!r} may not tap")
    if state.stage == 4:
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


def register_scrape(link: LinkState, *, seen: bool, trusted: bool, file_hash: str, today: date,
                    new_status: str | None = None, new_score: str | None = None) -> LinkState:
    if seen:
        return replace(
            link, live=True, trusted_misses=0, absent_since=None, last_miss_hash=None,
            norm_status=new_status if new_status is not None else link.norm_status,
            score=new_score if new_score is not None else link.score)
    if not trusted or link.via == "screenshot" or link.last_miss_hash == file_hash:
        return link
    return replace(link, live=False, trusted_misses=link.trusted_misses + 1,
                   absent_since=link.absent_since or today, last_miss_hash=file_hash)


def _real_score(score: str | None) -> bool:
    return score is not None and score.strip().casefold() not in _PLACEHOLDER_SCORES


def _cleared_enough(link: LinkState, today: date) -> bool:
    return (not link.live and link.norm_status in ("missing", "late")
            and link.trusted_misses >= MISSES_TO_RECEIVE and link.absent_since is not None
            and (today - link.absent_since).days >= MIN_DAYS_CLEARED)


def portal_decision(state: AssignmentState, links: Sequence[LinkState],
                    today: date) -> tuple[int, str | None]:
    """(stage, received_evidence) after weighing portal evidence (plan 10.2)."""
    live = [ln for ln in links if ln.live]
    if any(ln.norm_status == "missing" for ln in live):
        if state.stage == 4:
            return 1, None
        return state.stage, state.received_evidence
    if state.stage == 4:
        return 4, state.received_evidence
    trusted_links = [ln for ln in links if ln.via != "screenshot"]
    graded = any(ln.live and (ln.norm_status == "graded" or _real_score(ln.score))
                 for ln in trusted_links)
    if graded or any(_cleared_enough(ln, today) for ln in trusted_links):
        return 4, "portal"
    if any(ln.norm_status == "submitted" for ln in live):
        return max(state.stage, 3), state.received_evidence
    return state.stage, state.received_evidence
