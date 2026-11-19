"""Flask test-client harness for the /api/v2 tests (S4b). All data is SYNTHETIC."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from recon.ingest_support import make_db
from recon.report_support import ps, sgy
from recon.s4_support import NOW

PATH_VARS = ("SCHOOL_STATE_PATH", "SCHOOL_DB_PATH", "SCHOOL_FACTS_PATH", "SCHOOL_EMAIL_DIGEST", "SCHOOL_GC_PATH",
             "SCHOOL_SYNC_META_PATH", "SCHOOL_DASHBOARD_HTML", "SCHOOL_CALENDAR_PDF", "SCHOOL_DASHBOARD_CONFIG")
TOPIC = "synthetic-topic-9f3a"
ACTOR = {"role": "parent", "name": "Dana"}
KID_ACTOR = {"role": "kid", "name": "Alex"}


@dataclass
class Api:
    client: Any
    db: str
    state: Path
    clock: dict

    def conn(self) -> sqlite3.Connection:
        from school_dashboard.assignments import schema
        return schema.connect(self.db)

    def q(self, sql: str, args: Sequence[Any] = ()) -> list[sqlite3.Row]:
        c = self.conn()
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()

    def exec(self, sql: str, args: Sequence[Any] = ()) -> None:
        c = self.conn()
        try:
            c.execute(sql, args)
        finally:
            c.close()

    def get(self, path: str, **kw: Any):
        return self.client.get(path, **kw)

    def send(self, method: str, path: str, body: Any = None, *, ctype: str | None = "application/json",
             raw: bytes | str | None = None):
        data = raw if raw is not None else json.dumps(body)
        kw: dict[str, Any] = {"data": data}
        if ctype:
            kw["content_type"] = ctype
        return getattr(self.client, method)(path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any):
        return self.send("post", path, body, **kw)

    def put(self, path: str, body: Any = None, **kw: Any):
        return self.send("put", path, body, **kw)


def write_meta(state: Path, now=NOW, *, fresh: bool = True) -> None:
    ts = (now - timedelta(hours=1 if fresh else 72)).isoformat()
    entry = {"last_run": ts, "last_ok": ts, "last_result": "ok"}
    meta = {k: dict(entry) for k in ("recon_ingest", "sgy", "powerschool", "ixl")}
    (state / "sync_meta.json").write_text(json.dumps(meta))


def make_api(tmp_path: Path, monkeypatch, seed: Sequence[Mapping[str, Any]] = (), *,
             roster: Sequence[str] | None = ("Alex", "Sam"), enabled: bool = True, migrated: bool = True,
             meta: str = "fresh", patch_now: bool = True, setup: Mapping[str, Any] | None = None) -> Api:
    from school_dashboard import state
    from web.app import app
    for var in PATH_VARS:
        monkeypatch.delenv(var, raising=False)
    sdir = tmp_path / "state"
    sdir.mkdir()
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(sdir))
    if enabled:
        monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    else:
        monkeypatch.delenv("RECON_INGEST_ENABLED", raising=False)
    monkeypatch.delenv("RECON_PUSH_ENABLED", raising=False)
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)
    if roster is not None:
        cfg = tmp_path / "roster.json"
        cfg.write_text(json.dumps({"children": {n: {"grade": "6th", "school": "S"} for n in roster}}))
        monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.setattr(state, "_config_cache", None)
    db = make_db(sdir, {"seed": list(seed), **(setup or {})}, migrated=migrated)
    if meta != "none":
        write_meta(sdir, fresh=meta == "fresh")
    clock = {"now": NOW}
    if patch_now:
        monkeypatch.setattr("web.api_v2._now", lambda: clock["now"])
    app.config["TESTING"] = True
    return Api(app.test_client(), db, sdir, clock)


def one(due: str = "2026-11-19", **over: Any) -> list[dict]:
    """One Schoology-linked Alex assignment (id 1): due tomorrow -> bucket do_tonight on NOW."""
    return [sgy("Essay", "Math 6", "s1", due=due, **over)]


def data(resp) -> Any:
    body = resp.get_json()
    assert body is not None, resp.data
    return body


def err(resp, status: int, code: str) -> None:
    assert resp.status_code == status, (resp.status_code, resp.data[:200])
    body = resp.get_json()
    assert body["ok"] is False and body["error"] == code and body["data"] is None, body


__all__ = ["ACTOR", "KID_ACTOR", "TOPIC", "Api", "data", "err", "make_api", "one", "ps", "sgy", "write_meta"]
