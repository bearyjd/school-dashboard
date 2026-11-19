"""Read model for the v2 API: pure assembly of display dicts from repo rows (no I/O, no clock).

Flags are computed on read with the injected `now`; the stored flag column is never consulted. Nothing here
emits raw_json, external keys, miss hashes, notes or free-form URLs that could carry credentials.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from .adapters.base import clean_text
from .flags import compute_flag
from .health import INGEST_SOURCE, IngestHealth
from .model import AssignmentRecord, FlagResult, LinkRecord

DEFAULT_GRACE = 2
MAX_EVENTS = 200
_STAGE_LABELS = {1: "Assigned", 2: "Done at home", 3: "Turned in", 4: "Received"}
_CREDENTIAL_WORDS = ("token", "key", "secret", "password", "auth", "session", "sig", "signature")
_REMAINING = re.compile(r"^\s*(\d+)")
_NO_DAYS: frozenset[date] = frozenset()
_BUCKET_ORDER = ("needs_action", "do_tonight", "waiting_on_teacher")


@dataclass(frozen=True)
class ListFilters:
    kid: str | None = None
    bucket: str | None = None
    flag: str | None = None
    stage: int | None = None
    kind: str | None = None
    include_closed: bool = False
    limit: int = 200
    offset: int = 0


@dataclass(frozen=True)
class _Ctx:
    now: datetime
    no_school_by_kid: Mapping[str, frozenset[date]]
    grace_by_class: Mapping[tuple[str, str], int]
    stale_sources: frozenset[str]
    ingest: IngestHealth


def safe_url(url: str | None) -> str | None:
    """The URL when it is plain http(s) without userinfo or credential-looking parameters; else None."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        names = [n.casefold() for n, _ in (*parse_qsl(parts.query, keep_blank_values=True),
                                           *parse_qsl(parts.fragment, keep_blank_values=True))]
        userinfo = "@" in parts.netloc
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or userinfo or not host:
        return None
    if any(word in name for name in names for word in _CREDENTIAL_WORDS):
        return None
    return url


def _remaining(links: Sequence[LinkRecord]) -> int | None:
    for ln in links:
        match = _REMAINING.match(ln.raw_status or "")
        if match:
            return int(match.group(1))
    return None


def _practice_result(result: FlagResult, links: Sequence[LinkRecord]) -> FlagResult:
    count = _remaining(links)
    todo = count is not None and count > 0
    return FlagResult(flag="do_tonight" if todo else None, bucket="do_tonight" if todo else None,
                      unverified=result.unverified, unverified_sources=result.unverified_sources, info=())


def _closed(rec: AssignmentRecord) -> bool:
    s = rec.state
    return s.stage == 4 or (s.kind == "task" and s.closed_at is not None)


def _result(rec: AssignmentRecord, links: Sequence[LinkRecord], ctx: _Ctx) -> FlagResult:
    s = rec.state
    result = compute_flag(
        s, [ln.state for ln in links], now=ctx.now, no_school=ctx.no_school_by_kid.get(s.kid, _NO_DAYS),
        grace_days=ctx.grace_by_class.get((s.kid, s.class_key), DEFAULT_GRACE), stale_sources=ctx.stale_sources,
        ingest_ok=ctx.ingest.ok)
    return _practice_result(result, links) if s.kind == "practice" else result


def _label(stage: int, info: Sequence[str]) -> str:
    label = _STAGE_LABELS[stage]
    return f"{label} (unconfirmed)" if stage == 3 and "unconfirmed_paper" in info else label


def _link(ln: LinkRecord) -> dict[str, Any]:
    st = ln.state
    return {"id": ln.id, "source": st.source, "status": st.norm_status,
            "raw_status": clean_text(ln.raw_status, 160) or None, "score": clean_text(st.score, 160) or None,
            "live": st.live, "url": safe_url(st.url)}


def _item(rec: AssignmentRecord, links: Sequence[LinkRecord], ctx: _Ctx) -> dict[str, Any]:
    s, result = rec.state, _result(rec, links, ctx)
    return {
        "id": s.id, "kid": s.kid, "title": clean_text(s.title, 300), "class": clean_text(s.class_name, 120) or None,
        "due": s.due_date.isoformat() if s.due_date else None, "kind": s.kind, "stage": s.stage,
        "stage_label": _label(s.stage, result.info), "flag": result.flag, "bucket": result.bucket,
        "info": list(result.info), "unverified": result.unverified,
        "unverified_sources": list(result.unverified_sources), "portal_status": s.portal_status,
        "conflict": s.portal_conflict, "received_evidence": s.received_evidence,
        "possible_duplicate": rec.possible_duplicate, "links": [_link(ln) for ln in links]}


def _items(rows: Sequence[AssignmentRecord], links_by_id: Mapping[int, Sequence[LinkRecord]],
           ctx: _Ctx) -> list[tuple[AssignmentRecord, dict[str, Any]]]:
    out = [(r, _item(r, links_by_id.get(r.state.id or 0, ()), ctx)) for r in rows]
    return sorted(out, key=lambda p: (p[0].state.due_date is None, p[0].state.due_date or date.max,
                                      p[0].state.id or 0))


def _wanted(rec: AssignmentRecord, item: Mapping[str, Any], f: ListFilters) -> bool:
    return ((f.include_closed or not _closed(rec)) and (f.kid is None or item["kid"] == f.kid)
            and (f.bucket is None or item["bucket"] == f.bucket) and (f.flag is None or item["flag"] == f.flag)
            and (f.stage is None or item["stage"] == f.stage) and (f.kind is None or item["kind"] == f.kind))


def build_list(rows: Sequence[AssignmentRecord], links_by_id: Mapping[int, Sequence[LinkRecord]], *, now: datetime,
               no_school_by_kid: Mapping[str, frozenset[date]], grace_by_class: Mapping[tuple[str, str], int],
               stale_sources: frozenset[str], ingest: IngestHealth,
               filters: ListFilters) -> tuple[Mapping[str, Any], ...]:
    ctx = _Ctx(now, no_school_by_kid, grace_by_class, stale_sources, ingest)
    kept = [item for rec, item in _items(rows, links_by_id, ctx) if _wanted(rec, item, filters)]
    return tuple(kept[filters.offset:filters.offset + filters.limit])


def _event(e: Mapping[str, Any]) -> dict[str, Any]:
    return {"at": e["at"], "setter": e["setter"], "actor": clean_text(e["actor"], 40) or None, "kind": e["kind"],
            "from": e["from_value"], "to": e["to_value"], "detail": clean_text(e["detail"], 500) or None}


def build_detail(record: AssignmentRecord, links: Sequence[LinkRecord], events: Sequence[Mapping[str, Any]], *,
                 now: datetime, no_school_by_kid: Mapping[str, frozenset[date]],
                 grace_by_class: Mapping[tuple[str, str], int], stale_sources: frozenset[str],
                 ingest: IngestHealth) -> Mapping[str, Any]:
    """One item plus its events, newest first (`events` oldest first, as repo.events_for returns them)."""
    ctx = _Ctx(now, no_school_by_kid, grace_by_class, stale_sources, ingest)
    return {**_item(record, links, ctx), "events": [_event(e) for e in reversed(events)][:MAX_EVENTS]}


def _counts(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    flags = [i["flag"] for i in items]
    detail = {k: flags.count(k) for k in ("done_not_received", "disputed", "missing")}
    return {"do_tonight": flags.count("do_tonight"), "waiting_on_teacher": flags.count("waiting_on_teacher"),
            "needs_action": sum(detail.values()), "needs_action_detail": detail}


def _kid_summary(name: str, pairs: Sequence[tuple[AssignmentRecord, dict[str, Any]]]) -> dict[str, Any]:
    mine = [(r, i) for r, i in pairs if i["kid"] == name and not _closed(r)]
    practice = [i for _, i in mine if i["kind"] == "practice"]
    counts = _counts([i for _, i in mine if i["kind"] != "practice"])
    clear = (counts["needs_action"] == 0 and counts["do_tonight"] == 0
             and not any(p["bucket"] == "do_tonight" for p in practice))
    return {"name": name, "counts": counts, "practice": practice, "all_clear": clear}


def build_summary(rows: Sequence[AssignmentRecord], links_by_id: Mapping[int, Sequence[LinkRecord]], *,
                  kids: Sequence[str], now: datetime, no_school_by_kid: Mapping[str, frozenset[date]],
                  grace_by_class: Mapping[tuple[str, str], int], stale_sources: frozenset[str],
                  ingest: IngestHealth, warnings: Sequence[str]) -> Mapping[str, Any]:
    ctx = _Ctx(now, no_school_by_kid, grace_by_class, stale_sources, ingest)
    pairs = _items(rows, links_by_id, ctx)
    names = [*kids, *sorted({r.state.kid for r, _ in pairs} - set(kids))]
    unverified = sorted({s for _, i in pairs for s in i["unverified_sources"]}
                        | ({INGEST_SOURCE} if not ingest.ok else set()))
    notes = [*warnings]
    if not ingest.ok:
        notes.append("Assignment data may be out of date: the last merge run did not succeed")
    return {"kids": [_kid_summary(n, pairs) for n in names], "data_as_of": ingest.data_as_of.isoformat()
            if ingest.data_as_of else None, "ingest_ok": ingest.ok, "unverified_sources": unverified,
            "warnings": notes, "generated_at": now.isoformat()}
