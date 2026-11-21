"""`/api/v2` blueprint: parse -> validate -> service / read model -> envelope. No SQL and no business rules here."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import Any, Mapping

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import HTTPException, MethodNotAllowed

from school_dashboard import state, watchdog
from school_dashboard.assignments import flagsenv, readmodel, repo, service
from school_dashboard.kid_colors import resolve_colors
from school_dashboard.assignments.readmodel import ListFilters
from school_dashboard.assignments import repo_history
from web import api_v2_auth, api_v2_push
from web import api_v2_context as ctxmod
from web import api_v2_validate as V

bp = Blueprint("api_v2", __name__, url_prefix="/api/v2")
logger = logging.getLogger(__name__)
_CODE = re.compile(r"^[a-z_]{1,40}$")
MAX_PATH_ID = 2 ** 53
_HTTP_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 413: "payload_too_large",
               415: "unsupported_media_type", 500: "internal"}
_READ_METHODS = ("GET", "HEAD", "OPTIONS")


def _now() -> datetime:
    return datetime.now(watchdog.get_tz())


def _reply(payload: Mapping[str, Any], status: int = 200) -> Response:
    resp = jsonify(payload)
    resp.status_code = status
    return resp


def _ok(data: Any, **meta: Any) -> Response:
    return _reply({"ok": True, "data": data, "error": None, "meta": meta})


def _fail(code: str, status: int) -> Response:
    return _reply({"ok": False, "data": None, "error": code, "meta": {}}, status)


@bp.before_request
def _gate() -> Response | None:
    if request.endpoint != "api_v2.config" and not flagsenv.enabled("RECON_INGEST_ENABLED"):
        return _fail("recon_disabled", 404)
    if request.method in _READ_METHODS:
        return None
    no = api_v2_auth.check_write(request.headers.get(api_v2_auth.HEADER, ""), request.headers.getlist("X-Forwarded-For"), request.remote_addr)
    if no is None:
        return None
    resp = _fail(no.code, no.status)
    if no.retry_after:
        resp.headers["Retry-After"] = str(no.retry_after)
    return resp


def _in_v2() -> bool:
    return request.path == "/api/v2" or request.path.startswith("/api/v2/")


@bp.after_app_request
def _harden(resp: Response) -> Response:
    if _in_v2():
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.app_errorhandler(HTTPException)
def _http_error(exc: HTTPException) -> Response | HTTPException:
    """Framework errors (unknown route, wrong method, ...) under /api/v2 get the JSON envelope."""
    if not _in_v2():
        return exc
    resp = _fail(_HTTP_CODES.get(exc.code or 500, "error"), exc.code or 500)
    if isinstance(exc, MethodNotAllowed) and exc.valid_methods:
        resp.headers["Allow"] = ", ".join(exc.valid_methods)
    return resp


@bp.errorhandler(V.RequestError)
def _bad_request(exc: V.RequestError) -> Response:
    return _fail(exc.code, exc.status)


@bp.errorhandler(service.NotFound)
def _not_found(_: service.NotFound) -> Response:
    return _fail("not_found", 404)


@bp.errorhandler(service.Conflict)
def _conflict(exc: service.Conflict) -> Response:
    return _fail(exc.code if _CODE.match(exc.code) else "conflict", 409)


@bp.errorhandler(repo.NotMigratedError)
def _not_migrated(_: repo.NotMigratedError) -> Response:
    return _fail("not_migrated", 409)


@bp.errorhandler(repo.BusyError)
def _busy(_: repo.BusyError) -> Response:
    resp = _fail("busy", 503)
    resp.headers["Retry-After"] = "2"
    return resp


@bp.errorhandler(sqlite3.IntegrityError)
def _rejected(exc: sqlite3.IntegrityError) -> Response:
    logger.warning("api v2: write rejected by a validation rule (%s)", type(exc).__name__)
    return _fail("rejected", 409)


@bp.errorhandler(Exception)
def _internal(exc: Exception) -> Response | HTTPException:
    if isinstance(exc, HTTPException):
        return _http_error(exc)
    logger.error("api v2: unexpected %s", type(exc).__name__)
    return _fail("internal", 500)


def _read_body() -> Mapping[str, Any]:
    """Content type and size are checked before a byte is parsed; the read itself is capped."""
    V.check_media_type(request.mimetype)
    V.check_size(request.content_length)
    return V.parse_body(request.stream.read(V.MAX_BODY + 1))


def _links(conn: sqlite3.Connection, rows: Any) -> dict[int, list[Any]]:
    return repo.link_records(conn, [r.state.id or 0 for r in rows])


def _item(conn: sqlite3.Connection, ctx: ctxmod.ReadCtx, aid: int, *, events: bool = False) -> Mapping[str, Any]:
    state = repo.get_assignment(conn, aid)
    rec = next((r for r in repo.assignment_records(conn, state.kid, True) if r.state.id == aid), None) if state else None
    if rec is None:  # unknown, or merged away
        raise service.NotFound("assignment")
    links = repo.link_records(conn, [aid])[aid]
    if events:
        return readmodel.build_detail(rec, links, repo.events_for(conn, aid), **ctx.view())
    flt = ListFilters(include_closed=True)
    return readmodel.build_list([rec], {aid: links}, filters=flt, **ctx.view())[0]


def _counts(conn: sqlite3.Connection, ctx: ctxmod.ReadCtx, kid: str) -> Mapping[str, Any]:
    rows = repo.assignment_records(conn, kid, True)
    summary = readmodel.build_summary(rows, _links(conn, rows), kids=[kid], warnings=(), **ctx.view())
    return summary["kids"][0]["counts"]


def _check_id(aid: int) -> None:
    if not 1 <= aid <= MAX_PATH_ID:
        raise service.NotFound("assignment")


def _written(conn: sqlite3.Connection, now: datetime, aid: int, **extra: Any) -> Response:
    ctx = ctxmod.load(conn, now)
    item = _item(conn, ctx, aid)
    out = {"item": item, "counts": _counts(conn, ctx, str(item["kid"])), **extra}
    api_v2_push.kick(ctxmod.db_file(), now)
    return _ok(out)


@bp.get("/config")
def config() -> Response:
    """Public by design: the SPA reads `write_auth` (open/token/unconfigured) to know whether to ask for a token.

    The token value itself is never part of this response.
    """
    names = ctxmod.roster_names()
    explicit = {r["name"]: r["color"] for r in state.canonical_roster() if r.get("color")}
    colors = resolve_colors(names, explicit)  # same resolver and order as /api/overview
    return _ok({"enabled": flagsenv.enabled("RECON_INGEST_ENABLED"), "migrated": ctxmod.is_migrated(),
                "kids": [{"name": n, "color": colors[n]} for n in names],
                "grace_default": readmodel.DEFAULT_GRACE, "write_auth": api_v2_auth.mode(),
                "write_token_header": api_v2_auth.HEADER, "write_token_min_length": api_v2_auth.MIN_TOKEN_LEN})


@bp.get("/summary")
def summary() -> Response:
    now = _now()
    with ctxmod.connection(readonly=True) as conn:
        ctx = ctxmod.load(conn, now)
        rows = repo.assignment_records(conn, None, True)
        return _ok(readmodel.build_summary(rows, _links(conn, rows), kids=ctx.roster, warnings=(), **ctx.view()))


@bp.get("/assignments")
def assignments() -> Response:
    now = _now()
    with ctxmod.connection(readonly=True) as conn:
        ctx = ctxmod.load(conn, now)
        flt = V.parse_list_query(request.args, ctx.kids)
        rows = repo.assignment_records(conn, flt.kid, True)
        page = readmodel.build_page(rows, _links(conn, rows), filters=flt, **ctx.view())
        return _ok(page.items, count=len(page.items), total=page.total, limit=flt.limit, offset=flt.offset)


@bp.get("/assignments/<int:aid>")
def assignment(aid: int) -> Response:
    _check_id(aid)
    now = _now()
    with ctxmod.connection(readonly=True) as conn:
        item = _item(conn, ctxmod.load(conn, now), aid, events=True)
        return _ok({**item, "notes": service.list_notes(conn, aid)})


@bp.get("/classes")
def classes_get() -> Response:
    now = _now()
    with ctxmod.connection(readonly=True) as conn:
        ctx = ctxmod.load(conn, now)
        kid = V.parse_class_query(request.args, ctx.kids)
        return _ok(_class_rows(conn, ctx, kid), events=_class_events(conn, kid))


def _class_events(conn: sqlite3.Connection, kid: str) -> list[dict[str, Any]]:
    out = []
    for e in repo_history.class_events(conn, kid):
        try:
            changes = json.loads(e["changes"])
        except ValueError:
            changes = {}
        out.append({**e, "changes": changes})
    return out


def _class_rows(conn: sqlite3.Connection, ctx: ctxmod.ReadCtx, kid: str) -> list[dict[str, Any]]:
    out = []
    for row in repo.class_overview(conn, kid):
        grace = row["grace_school_days"]
        out.append({**row, "aliases": ctxmod.alias_list(row["aliases"]),
                    "effective_grace": readmodel.DEFAULT_GRACE if grace is None else grace})
    return out


@bp.post("/assignments/<int:aid>/stage")
def stage(aid: int) -> Response:
    _check_id(aid)
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_stage(body)
        service.tap(conn, aid, req.action, req.actor, now=now, no_school_rows=ctx.events)
        return _written(conn, now, aid)


@bp.post("/assignments/<int:aid>/teacher-confirmed")
def teacher_confirmed(aid: int) -> Response:
    _check_id(aid)
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_teacher_confirmed(body)
        V.require_parent(req.actor)
        service.teacher_confirmed(conn, aid, req.note, req.actor, now=now, no_school_rows=ctx.events)
        return _written(conn, now, aid)


@bp.post("/assignments/<int:aid>/merge")
def merge(aid: int) -> Response:
    _check_id(aid)
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_merge(body)
        V.require_parent(req.actor)
        into = service.merge(conn, aid, req.into, req.actor, now=now, no_school_rows=ctx.events,
                          force=req.force)
        return _written(conn, now, into)


@bp.post("/assignments/<int:aid>/split")
def split(aid: int) -> Response:
    _check_id(aid)
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_split(body)
        V.require_parent(req.actor)
        new_id = service.split(conn, aid, req.link_id, req.actor, now=now, no_school_rows=ctx.events)
        return _written(conn, now, new_id, source_id=aid)


@bp.post("/kids/<kid>/remerge")
def remerge(kid: str) -> Response:
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_remerge(body)
        V.require_parent(req.actor)
        name = V.parse_kid(kid, ctx.kids)
        result = service.remerge_kid(conn, name, req.actor, now=now, no_school_rows=ctx.events)
        fresh = ctxmod.load(conn, now)
        api_v2_push.kick(ctxmod.db_file(), now)
        return _ok({"merged": [list(p) for p in result.merged], "ambiguous_groups": result.ambiguous_groups,
                    "counts": _counts(conn, fresh, name)})


@bp.put("/classes")
def classes_put() -> Response:
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_class_put(body, ctx.kids)
        V.require_parent(req.actor)
        service.set_class(conn, req.kid, req.class_name, req.settings, req.actor, now=now,
                          no_school_rows=ctx.events)
        fresh = ctxmod.load(conn, now)
        api_v2_push.kick(ctxmod.db_file(), now)
        return _ok({"classes": _class_rows(conn, fresh, req.kid), "counts": _counts(conn, fresh, req.kid)})


@bp.post("/assignments/<int:aid>/note")
def note_create(aid: int) -> Response:
    """A draft only: the text and a mailto link for the parent to open themselves. Nothing is ever sent."""
    _check_id(aid)
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_note(body)
        V.require_parent(req.actor)
        n, created = service.create_note(conn, aid, req.actor, view=ctx.view())
        return _reply({"ok": True, "data": asdict(n), "error": None, "meta": {}}, 201 if created else 200)


@bp.patch("/assignments/<int:aid>/note/<int:nid>")
def note_status(aid: int, nid: int) -> Response:
    """Records what the parent says happened to a draft; the server never emails."""
    _check_id(aid)
    _check_id(nid)
    body = _read_body()
    with ctxmod.connection(readonly=False) as conn:
        req = V.parse_note_status(body)
        V.require_parent(req.actor)
        return _ok({"note": service.set_note_status(conn, aid, nid, req.status, req.actor)})
