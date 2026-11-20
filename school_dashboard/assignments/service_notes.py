"""Teacher-note drafts: build and store a draft for a flagged record; the parent reports what they did.

Nothing here (or anywhere in the app) sends mail: a draft is text plus a mailto link the parent opens themselves.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from . import notes, readmodel, repo, repo_history, repo_notes
from .model import AssignmentRecord
from .schooldays import school_today
from .service_types import Actor, Conflict, NotFound, load_open, who_from, write_txn

logger = logging.getLogger(__name__)
NOTE_STATUSES = ("sent_marked", "discarded")


@dataclass(frozen=True)
class NoteResult:
    id: int
    flag: str
    subject: str
    body: str
    teacher_email: str | None
    mailto: str | None
    portal_url: str | None


def _portal_url(item: Mapping[str, Any]) -> str | None:
    """The first live link whose URL passes the read model's safety filter (`item` links are already filtered)."""
    return next((ln["url"] for ln in item["links"] if ln["live"] and readmodel.safe_url(ln["url"])), None)


def create_note(conn: sqlite3.Connection, assignment_id: int, actor: Actor, *,
                view: Mapping[str, Any]) -> NoteResult:
    """`view` is the read context (`ReadCtx.view()`); the flag is computed from it inside the transaction."""
    who = who_from(actor, parent_only=True)
    now: datetime = view["now"]
    with write_txn(conn):
        state = load_open(conn, assignment_id)
        links = repo.link_records(conn, [assignment_id])[assignment_id]
        item = readmodel.build_list([AssignmentRecord(state)], {assignment_id: links}, filters=readmodel.ListFilters(
            include_closed=True), **view)[0]
        flag = item["flag"]
        if flag not in notes.NOTE_FLAGS:
            raise Conflict("no_note_for_flag")
        cfg = repo_history.class_settings_row(conn, state.kid, state.class_key) or {}
        email = cfg.get("teacher_email") if notes.valid_address(cfg.get("teacher_email")) else None
        marked = state.stage_since.astimezone(now.tzinfo).date() if state.stage in (2, 3) else None
        text = notes.draft(notes.NoteItem(state.title, state.class_name, marked), flag=flag,
                           parent_name=who.actor or "", kid=state.kid, teacher_name=cfg.get("teacher_name"),
                           today=school_today(now))
        nid = repo_notes.insert_note(conn, assignment_id, flag=flag, body=text.body, actor=who.actor or "", now=now)
    logger.info("recon note draft: flag=%s", flag)
    return NoteResult(nid, flag, text.subject, text.body, email, notes.mailto(email, text.subject, text.body),
                      _portal_url(item) if flag == "disputed" else None)


def set_note_status(conn: sqlite3.Connection, assignment_id: int, note_id: int, status: str,
                    actor: Actor) -> Mapping[str, Any]:
    """Record what the parent says happened to a draft (draft -> sent_marked | discarded)."""
    who_from(actor, parent_only=True)
    if status not in NOTE_STATUSES:
        raise Conflict("invalid_status")
    with write_txn(conn):
        row = repo_notes.get_note(conn, assignment_id, note_id)
        if row is None:
            raise NotFound("note")
        if row["status"] != "draft":
            raise Conflict("invalid_transition")
        repo_notes.set_status(conn, note_id, status)
    return {**row, "status": status}


def list_notes(conn: sqlite3.Connection, assignment_id: int) -> list[dict[str, Any]]:
    return repo_notes.notes_for(conn, assignment_id)
