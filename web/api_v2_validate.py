"""Pure request validation for /api/v2 (no web framework): strict schemas, closed enums, real ints, cleaned strings."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Collection, Mapping

from school_dashboard.assignments.adapters.base import clean_text
from school_dashboard.assignments.model import FLAGS, KINDS
from school_dashboard.assignments.readmodel import ListFilters
from school_dashboard.assignments.service_types import MAX_ACTOR_CHARS, Actor, ClassSettings

MAX_BODY = 8 * 1024
MAX_ID = 2 ** 63 - 1
MAX_ALIASES = 20
ACTIONS = ("done", "turned_in", "undo")
NOTE_STATUSES = ("sent_marked", "discarded")
BUCKETS = ("needs_action", "do_tonight", "waiting_on_teacher")
MAX_ALIASES_JSON = 2000
_DIGITS = re.compile(r"^[0-9]{1,9}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SETTING_KEYS = ("grace_school_days", "paper_only", "aliases", "teacher_name", "teacher_email")


class RequestError(Exception):
    """A rejected request: `status` is the HTTP status, `code` a fixed safe string."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class StageRequest:
    action: str
    actor: Actor


@dataclass(frozen=True)
class ConfirmRequest:
    note: str
    actor: Actor


@dataclass(frozen=True)
class MergeRequest:
    into: int
    actor: Actor
    force: bool = False


@dataclass(frozen=True)
class SplitRequest:
    link_id: int
    actor: Actor


@dataclass(frozen=True)
class RemergeRequest:
    actor: Actor


@dataclass(frozen=True)
class NoteRequest:
    actor: Actor


@dataclass(frozen=True)
class NoteStatusRequest:
    status: str
    actor: Actor


@dataclass(frozen=True)
class ClassRequest:
    kid: str
    class_name: str
    settings: ClassSettings
    actor: Actor


def check_media_type(mimetype: str | None) -> None:
    if mimetype != "application/json":
        raise RequestError(415, "unsupported_media_type")


def check_size(content_length: int | None) -> None:
    if content_length is not None and content_length > MAX_BODY:
        raise RequestError(413, "payload_too_large")


def parse_body(raw: bytes) -> Mapping[str, Any]:
    """The JSON object in `raw` (at most MAX_BODY bytes)."""
    if len(raw) > MAX_BODY:
        raise RequestError(413, "payload_too_large")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise RequestError(400, "invalid_json") from exc
    if not isinstance(parsed, dict):
        raise RequestError(400, "invalid_body")
    return parsed


def _only(body: Mapping[str, Any], allowed: Collection[str]) -> None:
    if any(k not in allowed for k in body):
        raise RequestError(400, "unknown_field")


def _text(value: Any, low: int, high: int) -> str:
    out = clean_text(value, high + 1)
    if not low <= len(out) <= high:
        raise RequestError(400, "invalid_field")
    return out


def _int(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise RequestError(400, "invalid_field")
    return value


def _actor(raw: Any) -> Actor:
    if not isinstance(raw, dict):
        raise RequestError(400, "invalid_actor")
    _only(raw, ("role", "name"))
    role = raw.get("role")
    name = clean_text(raw.get("name"), MAX_ACTOR_CHARS + 1)
    if role not in ("parent", "kid") or not 1 <= len(name) <= MAX_ACTOR_CHARS:
        raise RequestError(400, "invalid_actor")
    return Actor(str(role), name)


def _fields(body: Mapping[str, Any], fields: tuple[str, ...]) -> Actor:
    _only(body, (*fields, "actor"))
    return _actor(body.get("actor"))


def parse_stage(body: Mapping[str, Any]) -> StageRequest:
    actor = _fields(body, ("action",))
    action = body.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise RequestError(400, "invalid_field")
    return StageRequest(action, actor)


def parse_teacher_confirmed(body: Mapping[str, Any]) -> ConfirmRequest:
    actor = _fields(body, ("note",))
    return ConfirmRequest(_text(body.get("note"), 3, 500), actor)


def parse_merge(body: Mapping[str, Any]) -> MergeRequest:
    actor = _fields(body, ("into", "force"))
    force = body.get("force", False)
    if type(force) is not bool:
        raise RequestError(400, "invalid_field")
    return MergeRequest(_int(body.get("into"), 1, MAX_ID), actor, force)


def parse_split(body: Mapping[str, Any]) -> SplitRequest:
    actor = _fields(body, ("link_id",))
    return SplitRequest(_int(body.get("link_id"), 1, MAX_ID), actor)


def parse_remerge(body: Mapping[str, Any]) -> RemergeRequest:
    return RemergeRequest(_fields(body, ()))


def parse_note(body: Mapping[str, Any]) -> NoteRequest:
    return NoteRequest(_fields(body, ()))


def parse_note_status(body: Mapping[str, Any]) -> NoteStatusRequest:
    actor = _fields(body, ("status",))
    status = body.get("status")
    if not isinstance(status, str) or status not in NOTE_STATUSES:
        raise RequestError(400, "invalid_field")
    return NoteStatusRequest(status, actor)


def parse_kid(value: Any, kids: Collection[str]) -> str:
    if not isinstance(value, str) or value not in kids:
        raise RequestError(400, "invalid_kid")
    return value


def _aliases(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_ALIASES:
        raise RequestError(400, "invalid_field")
    names = tuple(_text(v, 1, 100) for v in value)
    if len(json.dumps(names, ensure_ascii=False)) > MAX_ALIASES_JSON:
        raise RequestError(400, "invalid_field")
    return names


def _email(body: Mapping[str, Any]) -> str | None:
    value = _optional_text(body, "teacher_email", 200)
    if value is not None and not _EMAIL.match(value):
        raise RequestError(400, "invalid_field")
    return value


def _optional_text(body: Mapping[str, Any], key: str, high: int) -> str | None:
    if body.get(key) is None:
        return None
    return _text(body[key], 1, high)


def _settings(body: Mapping[str, Any]) -> ClassSettings:
    grace = body.get("grace_school_days")
    paper = body.get("paper_only", False)
    if type(paper) is not bool:
        raise RequestError(400, "invalid_field")
    return ClassSettings(
        grace_school_days=None if grace is None else _int(grace, 0, 10), paper_only=paper,
        aliases=_aliases(body.get("aliases", [])), teacher_name=_optional_text(body, "teacher_name", 120),
        teacher_email=_email(body), fields=frozenset(k for k in _SETTING_KEYS if k in body))


def parse_class_put(body: Mapping[str, Any], kids: Collection[str]) -> ClassRequest:
    actor = _fields(body, ("kid", "class_name", "grace_school_days", "paper_only", "aliases", "teacher_name",
                           "teacher_email"))
    kid = parse_kid(body.get("kid"), kids)
    return ClassRequest(kid, _text(body.get("class_name"), 1, 120), _settings(body), actor)


def require_parent(actor: Actor) -> None:
    if actor.role != "parent":
        raise RequestError(403, "forbidden_role")


def _choice(args: Mapping[str, str], key: str, allowed: Collection[str]) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if value not in allowed:
        raise RequestError(400, "invalid_query")
    return value


def _number(args: Mapping[str, str], key: str, default: int, low: int, high: int) -> int:
    raw = args.get(key)
    if raw is None:
        return default
    if not _DIGITS.match(raw) or not raw.isascii() or not low <= int(raw) <= high:
        raise RequestError(400, "invalid_query")
    return int(raw)


def parse_list_query(args: Mapping[str, str], kids: Collection[str]) -> ListFilters:
    """Unknown query parameters are ignored; known ones are strict."""
    kid = parse_kid(args["kid"], kids) if "kid" in args else None
    stage = _number(args, "stage", 0, 1, 4) if "stage" in args else None
    closed = _choice(args, "include_closed", ("0", "1"))
    return ListFilters(
        kid=kid, bucket=_choice(args, "bucket", BUCKETS), flag=_choice(args, "flag", FLAGS), stage=stage,
        kind=_choice(args, "kind", KINDS), include_closed=closed == "1",
        limit=_number(args, "limit", 200, 1, 500), offset=_number(args, "offset", 0, 0, 10 ** 9))


def parse_class_query(args: Mapping[str, str], kids: Collection[str]) -> str:
    return parse_kid(args.get("kid"), kids)
