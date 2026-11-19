"""`/api/v2` blueprint: parse -> validate -> service / read model -> envelope. No SQL and no business rules here."""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from typing import Any, Mapping

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import HTTPException

from school_dashboard import watchdog
from school_dashboard.assignments import flagsenv, notify, readmodel, repo, service
from school_dashboard.assignments.readmodel import ListFilters
from web import api_v2_context as ctxmod
from web import api_v2_validate as V

bp = Blueprint("api_v2", __name__, url_prefix="/api/v2")
logger = logging.getLogger(__name__)
_CODE = re.compile(r"^[a-z_]{1,40}$")


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
    return None


@bp.after_app_request
def _no_store(resp: Response) -> Response:
    if request.path == "/api/v2" or request.path.startswith("/api/v2/"):
        resp.headers["Cache-Control"] = "no-store"
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


@bp.errorhandler(Exception)
def _internal(exc: Exception) -> Response | HTTPException:
    if isinstance(exc, HTTPException):
        return exc
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


def _push(conn: sqlite3.Connection, now: datetime) -> None:
    try:
        notify.send_pending(conn, now=now)
    except Exception as exc:  # best effort: the write already succeeded
        logger.error("api v2: push failed (%s)", type(exc).__name__)


def _written(conn: sqlite3.Connection, now: datetime, aid: int, **extra: Any) -> Response:
    ctx = ctxmod.load(conn, now)
    item = _item(conn, ctx, aid)
    out = {"item": item, "counts": _counts(conn, ctx, str(item["kid"])), **extra}
    _push(conn, now)
    return _ok(out)


@bp.get("/config")
def config() -> Response:
    names = ctxmod.roster_names()
    return _ok({"enabled": flagsenv.enabled("RECON_INGEST_ENABLED"), "migrated": ctxmod.is_migrated(),
                "kids": [{"name": n, "colorIndex": i} for i, n in enumerate(names)],
                "grace_default": readmodel.DEFAULT_GRACE})


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
        items = readmodel.build_list(rows, _links(conn, rows), filters=flt, **ctx.view())
        return _ok(items, count=len(items), limit=flt.limit, offset=flt.offset)


@bp.get("/assignments/<int:aid>")
def assignment(aid: int) -> Response:
    now = _now()
    with ctxmod.connection(readonly=True) as conn:
        return _ok(_item(conn, ctxmod.load(conn, now), aid, events=True))


@bp.get("/classes")
def classes_get() -> Response:
    now = _now()
    with ctxmod.connection(readonly=True) as conn:
        ctx = ctxmod.load(conn, now)
        kid = V.parse_class_query(request.args, ctx.kids)
        return _ok(_class_rows(conn, ctx, kid))


def _class_rows(conn: sqlite3.Connection, ctx: ctxmod.ReadCtx, kid: str) -> list[dict[str, Any]]:
    out = []
    for row in repo.class_overview(conn, kid):
        grace = row["grace_school_days"]
        out.append({**row, "aliases": ctxmod.alias_list(row["aliases"]),
                    "effective_grace": readmodel.DEFAULT_GRACE if grace is None else grace})
    return out


@bp.post("/assignments/<int:aid>/stage")
def stage(aid: int) -> Response:
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_stage(body)
        service.tap(conn, aid, req.action, req.actor, now=now, no_school_rows=ctx.events)
        return _written(conn, now, aid)


@bp.post("/assignments/<int:aid>/teacher-confirmed")
def teacher_confirmed(aid: int) -> Response:
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_teacher_confirmed(body)
        V.require_parent(req.actor)
        service.teacher_confirmed(conn, aid, req.note, req.actor, now=now, no_school_rows=ctx.events)
        return _written(conn, now, aid)


@bp.post("/assignments/<int:aid>/merge")
def merge(aid: int) -> Response:
    body, now = _read_body(), _now()
    with ctxmod.connection(readonly=False) as conn:
        ctx = ctxmod.load(conn, now)
        req = V.parse_merge(body)
        V.require_parent(req.actor)
        into = service.merge(conn, aid, req.into, req.actor, now=now, no_school_rows=ctx.events)
        return _written(conn, now, into)


@bp.post("/assignments/<int:aid>/split")
def split(aid: int) -> Response:
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
        pairs = service.remerge_kid(conn, name, req.actor, now=now, no_school_rows=ctx.events)
        fresh = ctxmod.load(conn, now)
        _push(conn, now)
        return _ok({"merged": [list(p) for p in pairs], "counts": _counts(conn, fresh, name)})


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
        _push(conn, now)
        return _ok({"classes": _class_rows(conn, fresh, req.kid), "counts": _counts(conn, fresh, req.kid)})
