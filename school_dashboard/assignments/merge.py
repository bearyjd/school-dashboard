"""Match incoming source records to assignments; combine link statuses."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from .model import AssignmentState, LinkState, SourceRecord
from .normalize import class_key, title_key

_NO_ALIASES: Mapping[str, str] = MappingProxyType({})
_SEVERITY = {"missing": 5, "late": 4, "open": 3, "submitted": 2, "graded": 1}
_RECEIVED = ("submitted", "graded")


@dataclass(frozen=True)
class MatchResult:
    kind: str  # link | key | relaxed | new | ambiguous
    assignment_id: int | None
    candidate_ids: tuple[int, ...] = ()


def _ids(cands: Sequence[AssignmentState]) -> tuple[int, ...]:
    return tuple(c.id for c in cands if c.id is not None)


def match(record: SourceRecord, candidates: Sequence[AssignmentState],
          link_index: Mapping[tuple[str, str, str], int],
          aliases: Mapping[str, str] = _NO_ALIASES) -> MatchResult:
    linked = link_index.get((record.source, record.kid, record.external_key))
    if linked is not None:
        return MatchResult("link", linked)
    rec_class = class_key(record.class_name, aliases)
    rec_title = title_key(record.title)
    same = [c for c in candidates
            if c.kid == record.kid and c.title_key == rec_title and c.due_date == record.due_date]
    full = [c for c in same if c.class_key == rec_class]
    if full and (rec_class or record.due_date is not None):
        return _single("key", full)
    if record.due_date is None:
        return MatchResult("new", None)
    relaxed = [c for c in same if not rec_class or not c.class_key]
    if not relaxed:
        return MatchResult("new", None)
    return _single("relaxed", relaxed)


def _single(kind: str, found: Sequence[AssignmentState]) -> MatchResult:
    if len(found) == 1:
        return MatchResult(kind, found[0].id)
    return MatchResult("ambiguous", None, _ids(found))


def combine_portal_status(links: Sequence[LinkState]) -> tuple[str, bool]:
    """(portal_status, conflict) from the live links; absent missing/late links mean cleared."""
    live = [ln.norm_status for ln in links if ln.live]
    votes = [s for s in live if s in _SEVERITY]
    if votes:
        status = max(votes, key=_SEVERITY.__getitem__)
        conflict = "missing" in votes and any(s in _RECEIVED for s in votes)
        return status, conflict
    if any(not ln.live and ln.norm_status in ("missing", "late") for ln in links):
        return "cleared", False
    return "none", False
