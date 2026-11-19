"""Class settings writes: partial updates, validation and the append-only audit trail."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from typing import Any, Mapping, Sequence

from . import repo, repo_history
from .adapters.base import clean_text
from .normalize import class_key
from .service_merge import remerge_in_txn
from .service_types import ClassSettings, Conflict, NotFound, Who, recompute, who_from, write_txn

logger = logging.getLogger(__name__)
Rows = Sequence[Mapping[str, Any]]
FIELDS = ("grace_school_days", "paper_only", "aliases", "teacher_name", "teacher_email")
MAX_ALIASES = 20
MAX_ALIASES_JSON = 2000
MAX_CHANGES_CHARS = 2000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def dump_aliases(names: Sequence[str]) -> str | None:
    return json.dumps(list(names), ensure_ascii=False) if names else None


def load_aliases(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [n for n in data if isinstance(n, str)] if isinstance(data, list) else []


def _clean_aliases(names: Sequence[str], canonical: str) -> list[str]:
    if len(names) > MAX_ALIASES:
        raise Conflict("invalid_class_settings")
    out: list[str] = []
    for raw in names:
        name = clean_text(raw, 101)
        key = class_key(name)
        if not name or len(name) > 100 or key == canonical:
            raise Conflict("invalid_class_settings")
        if all(class_key(n) != key for n in out):
            out.append(name)
    if len(json.dumps(out, ensure_ascii=False)) > MAX_ALIASES_JSON:
        raise Conflict("invalid_class_settings")
    return out


def _clean(values: Mapping[str, Any], canonical: str) -> dict[str, Any]:
    grace = values["grace_school_days"]
    if grace is not None and (isinstance(grace, bool) or not isinstance(grace, int) or not 0 <= grace <= 10):
        raise Conflict("invalid_class_settings")
    name = clean_text(values["teacher_name"], 121) if values["teacher_name"] is not None else None
    email = clean_text(values["teacher_email"], 201) if values["teacher_email"] is not None else None
    if (name is not None and len(name) > 120) or (email is not None and not (
            len(email) <= 200 and _EMAIL.match(email))):
        raise Conflict("invalid_class_settings")
    return {"grace_school_days": grace, "paper_only": bool(values["paper_only"]), "teacher_name": name or None,
            "teacher_email": email, "aliases": _clean_aliases(values["aliases"], canonical)}


def current_values(row: Mapping[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {"grace_school_days": row.get("grace_school_days"), "paper_only": bool(row.get("paper_only")),
            "aliases": load_aliases(row.get("aliases")), "teacher_name": row.get("teacher_name"),
            "teacher_email": row.get("teacher_email")}


def _merged(current: Mapping[str, Any], s: ClassSettings) -> dict[str, Any]:
    wanted = {"grace_school_days": s.grace_school_days, "paper_only": s.paper_only, "aliases": list(s.aliases),
              "teacher_name": s.teacher_name, "teacher_email": s.teacher_email}
    return {f: (wanted[f] if s.fields is None or f in s.fields else current[f]) for f in FIELDS}


def changes_json(old: Mapping[str, Any], new: Mapping[str, Any]) -> str | None:
    """JSON of {field: [old, new]} for the changed fields only; None when nothing changed."""
    changes = {f: [old[f], new[f]] for f in FIELDS if old[f] != new[f]}
    if not changes:
        return None
    text = json.dumps(changes, ensure_ascii=False)
    if len(text) > MAX_CHANGES_CHARS and "aliases" in changes:
        changes["aliases"] = [f"{len(old['aliases'])} aliases", f"{len(new['aliases'])} aliases"]
        text = json.dumps(changes, ensure_ascii=False)
    return text


def record_change(conn: sqlite3.Connection, kid: str, canonical: str, who: Who, now: datetime,
                  old: Mapping[str, Any], new: Mapping[str, Any]) -> None:
    text = changes_json(old, new)
    if text is not None:
        repo_history.append_class_event(conn, at=now, kid=kid, class_key=canonical, setter=who.setter,
                                        actor=who.actor, changes=text)


def set_class(conn: sqlite3.Connection, kid: str, class_name: str, settings: ClassSettings, actor: Any, *,
              now: datetime, no_school_rows: Rows) -> None:
    who = who_from(actor, parent_only=True)
    canonical = class_key(clean_text(class_name, 120))
    if not canonical:
        raise Conflict("invalid_class_settings")
    with write_txn(conn):
        if not repo.kid_has_assignments(conn, kid):
            raise NotFound("kid")
        old = current_values(repo_history.class_settings_row(conn, kid, canonical))
        new = _clean(_merged(old, settings), canonical)
        aliases = new["aliases"]
        repo.put_class_settings(conn, kid, canonical, grace=new["grace_school_days"], paper_only=new["paper_only"],
                                aliases=dump_aliases(aliases), teacher_name=new["teacher_name"],
                                teacher_email=new["teacher_email"])
        record_change(conn, kid, canonical, who, now, old, new)
        if {class_key(n) for n in aliases} != {class_key(n) for n in old["aliases"]}:
            remerge_in_txn(conn, kid, who, now)
        recompute(conn, kid, now, no_school_rows)
    logger.info("recon set_class: %d aliases stored", len(aliases))
