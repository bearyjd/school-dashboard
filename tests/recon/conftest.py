"""Shared fixtures for recon tests. ASSUMED repo signatures live ONLY in the helpers below."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from recon.support import FIXTURES, local, mk_link, mk_state

NOW = local(2026, 11, 18, 17, 0)


@pytest.fixture
def db_path(tmp_path):
    from school_dashboard import db
    p = tmp_path / "school.db"
    db.init_db(str(p))
    return str(p)


@pytest.fixture
def conn(db_path):
    from school_dashboard.assignments import schema
    c = schema.connect(db_path)
    schema.ensure_schema(c)
    yield c
    c.close()


def add_assignment(conn, **over) -> int:
    """ASSUMED: repo.insert_assignment(conn, state, *, now, notes=None, legacy_item_id=None, legacy_action_id=None) -> int"""
    from school_dashboard.assignments import repo
    extra = {k: over.pop(k) for k in ("notes", "legacy_item_id", "legacy_action_id") if k in over}
    return repo.insert_assignment(conn, mk_state(id=None, **over), now=NOW, **extra)


def add_link(conn, assignment_id, kid="Alex", **over):
    """ASSUMED: repo.upsert_link(conn, assignment_id, kid, link, *, now, raw_status=None, raw_json='{}')"""
    from school_dashboard.assignments import repo
    return repo.upsert_link(conn, assignment_id, kid, mk_link(**over), now=NOW, raw_status="", raw_json="{}")


def add_event(conn, assignment_id, kind="stage", setter="kid", **kw):
    """ASSUMED: repo.append_event(conn, assignment_id, *, at, setter, kind, actor=None, source=None, from_value=None, to_value=None, detail=None)"""
    from school_dashboard.assignments import repo
    return repo.append_event(conn, assignment_id, at=kw.pop("at", NOW), setter=setter, kind=kind, **kw)


def table_sha(path: str, table: str = "items") -> str:
    c = sqlite3.connect(path)
    try:
        rows = c.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    finally:
        c.close()
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()


def load_migration(name: str) -> dict:
    return json.loads((FIXTURES / "migration" / f"{name}.json").read_text())


def seed_legacy(db_path: str, state_path: Path, data: dict) -> None:
    """Write legacy items rows (explicit ids) and a state JSON holding action_items."""
    c = sqlite3.connect(db_path)
    with c:
        for it in data["items"]:
            c.execute(
                "INSERT INTO items (id, child, title, due_date, type, source, completed, completed_at, notes, created_at)"
                " VALUES (:id, :child, :title, :due_date, :type, :source, :completed, :completed_at, :notes, :created_at)", it)
    c.close()
    state_path.write_text(json.dumps({"children": {}, "action_items": data["action_items"]}, indent=2))


@pytest.fixture
def legacy_env(state_env, tmp_path, monkeypatch):
    """Isolated state dir with roster config; returns (state_dir, db_path, state_path, cfg_path)."""
    from school_dashboard import state
    golden = load_migration("golden")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"children": golden["roster"], "name_aliases": {}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.setattr(state, "_config_cache", None)
    from school_dashboard import db
    dbp = state_env / "school.db"
    db.init_db(str(dbp))
    return state_env, str(dbp), state_env / "school-state.json", cfg
