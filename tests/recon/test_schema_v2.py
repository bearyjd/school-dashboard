"""Schema v2: additive upgrade, trigger validation, append-only history."""
import logging
import sqlite3

import pytest

from recon.ingest_support import make_db
from recon.s4_support import NOW

TABLES = ("assignments", "source_links", "assignment_events", "push_outbox", "class_settings",
          "note_drafts", "recon_meta")
A = dict(kid="Alex", class_name="Math", class_key="math", title="T", title_key="t", kind="assignment", stage=1,
         stage_since="x", portal_status="none", created_at="x", updated_at="x")
L = dict(assignment_id=1, source="schoology", kid_external="Alex|k", external_key="k", norm_status="open",
         raw_json="{}", first_seen="x", last_seen="x", via="scrape")
E = dict(assignment_id=1, at="x", setter="parent", kind="stage")
P = dict(assignment_id=1, flag="missing", occurrence_at="x", reason="transition", created_at="x")
C = dict(kid="Alex", class_key="math")
BASE = {"assignments": A, "source_links": L, "assignment_events": E, "push_outbox": P, "class_settings": C}
ROW = {"assignments": 1, "source_links": 1, "assignment_events": 1, "push_outbox": 1, "class_settings": None}


def insert(conn, table, **over):
    row = {**BASE[table], **over}
    cols = ", ".join(row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))


def v2(tmp_path):
    from school_dashboard.assignments import schema
    c = schema.connect(make_db(tmp_path))
    insert(c, "assignments")
    for t in ("source_links", "assignment_events", "push_outbox", "class_settings"):
        insert(c, t)
    return c


def v1(tmp_path):
    from school_dashboard import db
    from school_dashboard.assignments import schema
    path = str(tmp_path / "v1.db")
    db.init_db(path)
    c = schema.connect(path)
    schema._MIGRATIONS[1](c)
    c.execute("PRAGMA user_version=1")
    return c


def dump(c):
    return {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in TABLES}


def test_schema_version_is_three():
    from school_dashboard.assignments import schema
    assert schema.SCHEMA_VERSION == 3


BAD = [("assignments", "kind", "bogus"), ("assignments", "stage", 5), ("assignments", "stage", 0),
       ("assignments", "portal_status", "x"), ("assignments", "flag", "nope"), ("assignments", "kid", ""),
       ("assignments", "kid", "k" * 81), ("assignments", "title", ""), ("assignments", "title", "t" * 301),
       ("assignments", "class_name", "c" * 121), ("assignments", "notes", "n" * 2001),
       ("source_links", "source", "Bad Source"), ("source_links", "source", ""), ("source_links", "source", "s" * 33),
       ("source_links", "source", "UPPER"), ("source_links", "norm_status", "x"), ("source_links", "via", "email"),
       ("source_links", "url", "u" * 2001), ("source_links", "raw_json", "j" * 16385),
       ("assignment_events", "setter", "robot"), ("assignment_events", "kind", "weird"),
       ("assignment_events", "actor", "a" * 41), ("assignment_events", "detail", "d" * 501),
       ("push_outbox", "status", "x"), ("push_outbox", "flag", "x"),
       ("class_settings", "grace_school_days", 11), ("class_settings", "grace_school_days", -1),
       ("class_settings", "paper_only", 2), ("class_settings", "aliases", "a" * 4001),
       ("class_settings", "teacher_name", "n" * 121), ("class_settings", "teacher_email", "e" * 201)]
GOOD = [("assignments", "flag", "missing"), ("assignments", "kid", "k" * 80), ("assignments", "title", "t" * 300),
        ("assignments", "class_name", "c" * 120), ("assignments", "notes", "n" * 2000),
        ("source_links", "source", "a-b_9" + "z" * 27), ("source_links", "via", "legacy"),
        ("source_links", "via", "manual"), ("source_links", "via", "screenshot"), ("source_links", "url", "u" * 2000),
        ("source_links", "raw_json", "j" * 16384), ("assignment_events", "kind", "split"),
        ("assignment_events", "actor", "a" * 40), ("assignment_events", "detail", "d" * 500),
        ("push_outbox", "status", "suppressed"), ("class_settings", "grace_school_days", 0),
        ("class_settings", "grace_school_days", 10), ("class_settings", "paper_only", 1),
        ("class_settings", "aliases", "a" * 4000), ("class_settings", "teacher_email", "e" * 200)]


@pytest.mark.parametrize("table,col,val", BAD)
def test_insert_rule_rejects_bad(tmp_path, table, col, val):
    c = v2(tmp_path)
    over = {col: val}
    if table == "class_settings":
        over["class_key"] = "other"
    with pytest.raises(sqlite3.IntegrityError):
        insert(c, table, **over)


@pytest.mark.parametrize("table,col,val", GOOD)
def test_insert_rule_accepts_good(tmp_path, table, col, val):
    c = v2(tmp_path)
    over = {col: val}
    if table == "source_links":
        over["kid_external"] = "Alex|other"
    if table == "class_settings":
        over["class_key"] = "other"
    if table == "push_outbox":
        over["occurrence_at"] = "y"
    insert(c, table, **over)


@pytest.mark.parametrize("table,col,val", [b for b in BAD if b[0] != "class_settings"][:30])
def test_update_rule_rejects_bad(tmp_path, table, col, val):
    c = v2(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(f"UPDATE {table} SET {col} = ? WHERE id = 1", (val,))


def test_update_class_settings_rejects_bad_grace(tmp_path):
    c = v2(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE class_settings SET grace_school_days = 99")
    c.execute("UPDATE class_settings SET grace_school_days = 3")


def test_update_accepts_good_value(tmp_path):
    c = v2(tmp_path)
    c.execute("UPDATE assignments SET flag = 'missing', title = 'ok' WHERE id = 1")


def test_events_are_append_only_and_nothing_deletes(tmp_path):
    c = v2(tmp_path)
    for sql in ("UPDATE assignment_events SET detail = 'x'", "DELETE FROM assignment_events",
                "DELETE FROM assignments", "DELETE FROM source_links"):
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql)
    assert c.execute("SELECT COUNT(*) FROM assignment_events").fetchone()[0] == 1


def populated_v1(tmp_path, **over):
    c = v1(tmp_path)
    insert(c, "assignments", **over)
    insert(c, "assignments", title="Second", title_key="second")
    insert(c, "source_links", source="Odd Source!")
    insert(c, "assignment_events", detail="d" * 600)
    insert(c, "push_outbox")
    insert(c, "class_settings", aliases='["Alg"]')
    c.execute("INSERT INTO recon_meta VALUES ('migrated_at', 'x')")
    return c


def test_upgrade_keeps_every_row_identical_and_is_idempotent(tmp_path):
    from school_dashboard.assignments import schema
    c = populated_v1(tmp_path)
    before = dump(c)
    assert schema.user_version(c) == 1
    assert schema.ensure_schema(c) == schema.SCHEMA_VERSION
    after = dump(c)
    assert {t: [r[:len(b)] for r, b in zip(after[t], before[t])] for t in TABLES} == before
    assert all(len(after[t]) == len(before[t]) for t in TABLES)
    assert [r[-1] for r in after["assignments"]] == [None, None]
    assert schema.user_version(c) == schema.SCHEMA_VERSION
    assert schema.ensure_schema(c) == schema.SCHEMA_VERSION
    assert dump(c) == after


def test_upgrade_joins_an_open_transaction(tmp_path):
    from school_dashboard.assignments import schema
    c = v1(tmp_path)
    c.execute("BEGIN IMMEDIATE")
    schema.ensure_schema(c)
    c.execute("ROLLBACK")
    assert schema.user_version(c) == 1


def test_upgrade_never_rebuilds_v1_tables(tmp_path):
    from school_dashboard.assignments import schema
    c = populated_v1(tmp_path)
    before = {r["name"]: r["sql"] for r in c.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")}
    schema.ensure_schema(c)
    after = {r["name"]: r["sql"] for r in c.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")}
    assert set(after) - set(before) == {"class_settings_events"}  # the append-only audit log is the only new table
    assert all(after[n].startswith(before[n].rstrip(")")) or "merged_into" in after[n] for n in before)


def test_legacy_rows_outside_limits_upgrade_and_are_counted(tmp_path, caplog):
    from school_dashboard.assignments import schema
    c = populated_v1(tmp_path, title="t" * 400)
    caplog.set_level(logging.INFO)
    schema.ensure_schema(c)
    assert schema.legacy_violations(c) == 3  # one assignment, one link, one event
    assert "t" * 50 not in caplog.text and "Odd Source" not in caplog.text
    c.execute("UPDATE assignments SET flag = 'missing' WHERE id = 1")  # untouched legacy value still updates
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE assignments SET title = ? WHERE id = 1", ("u" * 400,))


def test_clean_database_has_no_legacy_violations(tmp_path):
    assert schema_count(v2(tmp_path)) == 0


def schema_count(c):
    from school_dashboard.assignments import schema
    return schema.legacy_violations(c)


def test_ingest_upgrades_a_v1_database_and_runs(tmp_path):
    from school_dashboard.assignments import ingest, schema
    c = v1(tmp_path)
    c.execute("INSERT INTO recon_meta VALUES ('migrated_at', 'x')")
    c.close()
    report = ingest.run(str(tmp_path / "v1.db"), [], file_hashes={}, now=NOW, no_school_rows=[])
    assert report.ok
    c = schema.connect(str(tmp_path / "v1.db"))
    assert schema.user_version(c) == schema.SCHEMA_VERSION


def test_event_kinds_cover_everything_written():
    from school_dashboard.assignments import model
    assert set(model.EVENT_KINDS) == {"stage", "portal_status", "flag", "edit", "merge", "split"}
    assert set(model.LINK_VIAS) == {"scrape", "screenshot", "manual", "legacy"}
