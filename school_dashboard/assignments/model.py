"""Vocabularies and frozen dataclasses shared by the reconciliation engine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

PORTAL_STATUSES = ("none", "open", "submitted", "graded", "missing", "late", "cleared")
SETTERS = ("parent", "kid", "portal", "email", "system", "migration")
KINDS = ("assignment", "task", "practice")
EVENT_KINDS = ("stage", "portal_status", "flag", "edit", "merge", "split")
LINK_VIAS = ("scrape", "screenshot", "manual", "legacy")
PUSH_STATUSES = ("pending", "sent", "failed", "suppressed")
FLAGS = ("disputed", "missing", "done_not_received", "waiting_on_teacher", "do_tonight", "upcoming")
BUCKETS = {
    "do_tonight": "do_tonight",
    "waiting_on_teacher": "waiting_on_teacher",
    "done_not_received": "needs_action",
    "disputed": "needs_action",
    "missing": "needs_action",
}


@dataclass(frozen=True)
class SourceRecord:
    """Adapter output: one assignment as a single source reports it."""

    source: str
    kid: str
    class_name: str | None
    title: str
    due_date: date | None
    portal_status: str
    raw_status: str
    score: str | None
    link: str | None
    external_key: str
    raw: Mapping[str, Any]
    via: str = "scrape"  # scrape | screenshot | manual


@dataclass(frozen=True)
class LinkState:
    """One source_links row as the engine sees it."""

    source: str
    external_key: str
    norm_status: str
    score: str | None = None
    url: str | None = None
    live: bool = True  # present in the latest trusted scrape
    trusted_misses: int = 0
    absent_since: date | None = None
    last_miss_hash: str | None = None
    via: str = "scrape"


@dataclass(frozen=True)
class AssignmentState:
    id: int | None
    kid: str
    class_name: str | None
    class_key: str
    title: str
    title_key: str
    due_date: date | None
    kind: str
    stage: int
    stage_since: datetime
    received_evidence: str | None = None
    portal_status: str = "none"
    portal_conflict: bool = False
    closed_at: datetime | None = None
    paper_only: bool = False
    data_problem: bool = False  # a stored date was unreadable and replaced (see repo._to_state)
    dismissed_at: datetime | None = None

    @property
    def inactive(self) -> bool:
        return self.dismissed_at is not None or self.stage == 4 or (self.kind == "task" and self.closed_at is not None)


@dataclass(frozen=True)
class FlagResult:
    flag: str | None  # None when closed or exempt
    bucket: str | None  # do_tonight | waiting_on_teacher | needs_action | None
    unverified: bool
    unverified_sources: tuple[str, ...]
    info: tuple[str, ...]  # late, cleared_unconfirmed, unconfirmed_paper, conflict, no_due_date


@dataclass(frozen=True)
class LinkRecord:
    """A source link as the read model shows it (id and raw status included)."""

    id: int
    raw_status: str | None
    state: LinkState


@dataclass(frozen=True)
class AssignmentRecord:
    """An assignment plus the read-only facts the read model needs."""

    state: AssignmentState
    possible_duplicate: bool = False
