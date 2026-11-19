"""Shared types and helpers of the service layer: errors, actors, the one-transaction wrapper."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Mapping, Sequence

from . import ingest_kid, repo, schema
from .adapters.base import clean_text
from .model import AssignmentState

MAX_ACTOR_CHARS = 40
NOT_MIGRATED = "legacy items are not migrated; run: school-state assignments migrate"


class NotFound(LookupError):
    """The assignment (or link) does not exist."""


class Conflict(RuntimeError):
    """The request is valid but the record's state forbids it; `code` is a fixed, safe string."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Actor:
    role: str  # parent | kid
    name: str


@dataclass(frozen=True)
class ClassSettings:
    grace_school_days: int | None = None
    paper_only: bool = False
    aliases: tuple[str, ...] = ()
    teacher_name: str | None = None
    teacher_email: str | None = None
    fields: frozenset[str] | None = None  # None: every field is replaced; a set: only these (partial update)


@dataclass(frozen=True)
class RemergeResult:
    merged: tuple[tuple[int, int], ...]
    ambiguous_groups: int


@dataclass(frozen=True)
class Who:
    """Who an event is attributed to: `setter` is an events setter, `actor` the cleaned name (or None)."""

    setter: str
    actor: str | None


def who_from(actor: Actor | None, *, parent_only: bool = False) -> Who:
    """Validate an actor and turn it into an attribution; None is the system."""
    if actor is None:
        return Who("system", None)
    name = clean_text(actor.name, MAX_ACTOR_CHARS + 1)
    if actor.role not in ("parent", "kid") or not name or len(name) > MAX_ACTOR_CHARS:
        raise Conflict("bad_actor")
    if parent_only and actor.role != "parent":
        raise Conflict("forbidden_role")
    return Who(actor.role, name)


@contextmanager
def write_txn(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """require_migrated, then ONE BEGIN IMMEDIATE ... COMMIT (schema upgraded inside it)."""
    try:
        repo.require_migrated(conn)
    except sqlite3.OperationalError as exc:  # the recon tables are not there at all
        raise repo.NotMigratedError(NOT_MIGRATED) from exc
    with repo.transaction(conn):
        schema.ensure_schema(conn)
        yield conn


def load_open(conn: sqlite3.Connection, assignment_id: int) -> AssignmentState:
    state = repo.get_assignment(conn, assignment_id)
    if state is None:
        raise NotFound("assignment")
    if repo.merged_into(conn, assignment_id) is not None:
        raise Conflict("merged_away")
    return state


def recompute(conn: sqlite3.Connection, kid: str, now: datetime, no_school_rows: Sequence[Mapping[str, Any]]) -> None:
    """Flags and outbox for the kid, inside the caller's transaction."""
    baseline = repo.get_meta(conn, "baseline_at") is None
    ingest_kid.recompute_kid(conn, kid, now=now, no_school_rows=no_school_rows, baseline=baseline)
