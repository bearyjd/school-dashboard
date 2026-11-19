"""schema.py + repo.py (S2): DDL, connection policy, append-only history, outbox, transactions."""
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from recon.conftest import NOW, add_assignment, add_event, add_link, table_sha
from recon.support import local, mk_link
from school_dashboard.assignments import repo, schema

PKG = Path(__file__).resolve().parents[2] / "school_dashboard" / "assignments"
TABLES = {"assignments", "source_links", "assignment_events", "class_settings", "note_drafts", "push_outbox", "recon_meta"}


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


# ---- connect / ensure_schema ----
def test_schema_version_is_one():
    assert schema.SCHEMA_VERSION == 1


def test_connect_enables_foreign_keys(db_path):
    assert schema.connect(db_path).execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_sets_busy_timeout_5000(db_path):
    assert schema.connect(db_path).execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_connect_uses_wal(db_path):
    assert schema.connect(db_path).execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_connect_uses_row_factory_and_autocommit_mode(db_path):
    c = schema.connect(db_path)
    assert c.row_factory is sqlite3.Row and c.isolation_level is None


def test_readonly_connection_rejects_writes(db_path):
    c = schema.connect(db_path, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        c.execute("INSERT INTO items (child, title) VALUES ('Alex', 'x')")


def test_readonly_connection_does_not_switch_to_wal(db_path):
    schema.connect(db_path, readonly=True).close()
    assert sqlite3.connect(db_path).execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"


def test_ensure_schema_returns_version_and_sets_user_version(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION


def test_ensure_schema_creates_all_plan_tables(conn):
    assert TABLES <= _tables(conn)


def test_ensure_schema_is_idempotent(conn):
    assert schema.ensure_schema(conn) == schema.ensure_schema(conn) == schema.SCHEMA_VERSION


def test_ensure_schema_leaves_items_untouched(db_path):
    c = sqlite3.connect(db_path)
    c.execute("INSERT INTO items (child, title) VALUES ('Alex', 'x')"); c.commit(); c.close()
    before = table_sha(db_path)
    c = schema.connect(db_path); schema.ensure_schema(c); c.close()
    assert table_sha(db_path) == before


def test_ensure_schema_keeps_existing_events_and_digests_tables(conn):
    assert {"events", "digests", "items"} <= _tables(conn)


def test_assignments_has_no_paper_only_column(conn):
    assert "paper_only" not in _cols(conn, "assignments")


def test_class_settings_owns_paper_only(conn):
    assert "paper_only" in _cols(conn, "class_settings")


def test_push_outbox_and_recon_meta_exist_with_expected_columns(conn):
    assert {"occurrence_at", "reason", "status", "attempts", "last_error"} <= _cols(conn, "push_outbox")
    assert {"key", "value"} <= _cols(conn, "recon_meta")


def test_stage_check_constraint_rejects_stage_5(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add_assignment(conn, stage=5)


def test_portal_status_cleared_is_storable(conn):
    aid = add_assignment(conn, portal_status="cleared")
    assert repo.get_assignment(conn, aid).portal_status == "cleared"


def test_event_for_missing_assignment_violates_foreign_key(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add_event(conn, 9999)


def test_legacy_item_id_is_unique(conn):
    add_assignment(conn, legacy_item_id=7)
    with pytest.raises(sqlite3.IntegrityError):
        add_assignment(conn, title="Other", title_key="other", legacy_item_id=7)


# ---- assignments ----
def test_insert_then_get_round_trips_the_state(conn):
    aid = add_assignment(conn, title="Quiz", title_key="quiz", due_date="2026-11-20")
    got = repo.get_assignment(conn, aid)
    assert (got.id, got.kid, got.title, got.due_date.isoformat(), got.stage) == (aid, "Alex", "Quiz", "2026-11-20", 1)


def test_get_assignment_unknown_id_is_none(conn):
    assert repo.get_assignment(conn, 12345) is None


def test_update_assignment_persists_new_stage(conn):
    from dataclasses import replace
    aid = add_assignment(conn)
    repo.update_assignment(conn, replace(repo.get_assignment(conn, aid), stage=3), now=NOW)
    assert repo.get_assignment(conn, aid).stage == 3


def test_update_assignment_writes_updated_at(conn):
    from dataclasses import replace
    aid = add_assignment(conn)
    repo.update_assignment(conn, replace(repo.get_assignment(conn, aid), stage=2), now=local(2026, 11, 19, 9, 0))
    row = conn.execute("SELECT updated_at FROM assignments WHERE id=?", (aid,)).fetchone()
    assert "2026-11-19" in row["updated_at"]


def test_list_assignments_filters_by_kid(conn):
    add_assignment(conn, kid="Alex"); add_assignment(conn, kid="Sam", title="B", title_key="b")
    assert [a.kid for a in repo.list_assignments(conn, kid="Sam")] == ["Sam"]


def test_list_assignments_hides_closed_by_default(conn):
    add_assignment(conn, stage=4, received_evidence="migration", closed_at=NOW)
    assert repo.list_assignments(conn) == []


def test_list_assignments_can_include_closed(conn):
    add_assignment(conn, stage=4, received_evidence="migration", closed_at=NOW)
    assert len(repo.list_assignments(conn, include_closed=True)) == 1


def test_paper_only_comes_from_class_settings(conn):
    aid = add_assignment(conn, class_key="math 6", paper_only=False)
    conn.execute("INSERT INTO class_settings (kid, class_key, paper_only) VALUES ('Alex', 'math 6', 1)")
    assert repo.get_assignment(conn, aid).paper_only is True


# ---- links / events / meta ----
def test_upsert_link_twice_keeps_one_row_and_latest_status(conn):
    aid = add_assignment(conn)
    add_link(conn, aid, norm_status="open"); add_link(conn, aid, norm_status="graded", score="90")
    links = repo.links_for(conn, aid)
    assert [(l.norm_status, l.score) for l in links] == [("graded", "90")]


def test_links_for_returns_trusted_misses_and_absent_since(conn):
    from datetime import date
    aid = add_assignment(conn)
    add_link(conn, aid, norm_status="missing", live=False, trusted_misses=2, absent_since=date(2026, 11, 18), last_miss_hash="h")
    got = repo.links_for(conn, aid)[0]
    assert (got.live, got.trusted_misses, got.absent_since, got.last_miss_hash) == (False, 2, date(2026, 11, 18), "h")


def test_events_are_returned_in_append_order(conn):
    aid = add_assignment(conn)
    for i, kind in enumerate(["stage", "flag", "merge"]):
        add_event(conn, aid, kind=kind, at=local(2026, 11, 18, 10 + i))
    assert [e["kind"] for e in repo.events_for(conn, aid)] == ["stage", "flag", "merge"]


def test_event_records_setter_and_values(conn):
    aid = add_assignment(conn)
    add_event(conn, aid, setter="parent", from_value="1", to_value="2", actor="mom")
    e = repo.events_for(conn, aid)[0]
    assert (e["setter"], e["from_value"], e["to_value"], e["actor"]) == ("parent", "1", "2", "mom")


def test_get_meta_missing_key_returns_default(conn):
    assert repo.get_meta(conn, "nope", "dflt") == "dflt"


def test_set_meta_overwrites_value(conn):
    repo.set_meta(conn, "k", "a"); repo.set_meta(conn, "k", "b")
    assert repo.get_meta(conn, "k") == "b"


def test_require_migrated_raises_without_marker(conn):
    with pytest.raises(repo.NotMigratedError):
        repo.require_migrated(conn)


def test_require_migrated_passes_once_migrated_at_is_set(conn):
    repo.set_meta(conn, "migrated_at", "2026-11-18T12:00:00-05:00")
    repo.require_migrated(conn)


# ---- append-only / no-delete source greps ----
def _sources():
    files = sorted(PKG.glob("*.py"))
    assert files, "school_dashboard/assignments does not exist yet"
    return {p.name: p.read_text() for p in files}


def test_nothing_updates_or_deletes_assignment_events():
    pat = re.compile(r"(UPDATE\s+assignment_events|DELETE\s+FROM\s+assignment_events)", re.I)
    assert [n for n, s in _sources().items() if n != "schema.py" and pat.search(s)] == []


def test_nothing_deletes_assignments_or_source_links():
    pat = re.compile(r"DELETE\s+FROM\s+(assignments|source_links)\b", re.I)
    assert [n for n, s in _sources().items() if pat.search(s)] == []


def test_repo_module_exists_and_has_no_event_mutation():
    src = _sources()["repo.py"]
    assert not re.search(r"assignment_events[^;\"']*\b(UPDATE|DELETE)\b|\b(UPDATE|DELETE)\b[^;\"']*assignment_events", src, re.I)


# ---- push outbox ----
T1, T2 = local(2026, 11, 18, 12), local(2026, 11, 19, 12)


def _push(conn, aid, flag="missing", at=T1, reason="transition"):
    repo.enqueue_push(conn, aid, flag, at, reason)


def _pending(conn):
    return list(repo.pending_pushes(conn))


def test_enqueue_push_same_occurrence_twice_creates_one_row(conn):
    aid = add_assignment(conn)
    _push(conn, aid); _push(conn, aid)
    assert len(_pending(conn)) == 1


def test_enqueue_push_reentry_with_new_occurrence_creates_second_row(conn):
    aid = add_assignment(conn)
    _push(conn, aid, at=T1); _push(conn, aid, at=T2)
    assert len(_pending(conn)) == 2


def test_enqueue_push_different_flags_are_separate_rows(conn):
    aid = add_assignment(conn)
    _push(conn, aid, "missing"); _push(conn, aid, "disputed")
    assert len(_pending(conn)) == 2


def test_baseline_rows_are_suppressed_and_never_pending(conn):
    aid = add_assignment(conn)
    _push(conn, aid, reason="baseline")
    assert _pending(conn) == []
    assert conn.execute("SELECT status FROM push_outbox").fetchone()["status"] == "suppressed"


def test_pending_pushes_lists_transition_rows_with_ids(conn):
    aid = add_assignment(conn)
    _push(conn, aid)
    row = _pending(conn)[0]
    assert (row["assignment_id"], row["flag"], row["reason"]) == (aid, "missing", "transition")


def test_failed_then_sent_push_is_retried_once_and_stays_one_row(conn):
    aid = add_assignment(conn)
    _push(conn, aid)
    pid = _pending(conn)[0]["id"]
    repo.mark_push(conn, pid, "failed", now=NOW, error="ntfy 502")
    assert [r["id"] for r in _pending(conn)] == [pid]           # failed rows are retried
    repo.mark_push(conn, pid, "sent", now=NOW)
    assert _pending(conn) == [] and conn.execute("SELECT COUNT(*) FROM push_outbox").fetchone()[0] == 1


def test_failed_push_counts_attempts_and_keeps_error(conn):
    aid = add_assignment(conn)
    _push(conn, aid)
    pid = _pending(conn)[0]["id"]
    repo.mark_push(conn, pid, "failed", now=NOW, error="ntfy 502")
    row = conn.execute("SELECT attempts, last_error FROM push_outbox WHERE id=?", (pid,)).fetchone()
    assert (row["attempts"], row["last_error"]) == (1, "ntfy 502")


def test_reentry_after_sent_push_is_pending_again(conn):
    aid = add_assignment(conn)
    _push(conn, aid, at=T1)
    repo.mark_push(conn, _pending(conn)[0]["id"], "sent", now=NOW)
    _push(conn, aid, at=T2)
    assert len(_pending(conn)) == 1


# ---- transactions / concurrency ----
def test_transaction_commits_on_success(db_path, conn):
    with repo.transaction(conn):
        add_assignment(conn)
    other = schema.connect(db_path)
    assert other.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 1


def test_transaction_rolls_back_on_error(db_path, conn):
    with pytest.raises(RuntimeError):
        with repo.transaction(conn):
            add_assignment(conn)
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 0


def test_busy_error_is_a_runtime_error():
    assert issubclass(repo.BusyError, RuntimeError)


def test_second_writer_gets_busy_error_while_ingest_transaction_is_open(db_path, conn):
    aid = add_assignment(conn)
    tap = schema.connect(db_path)
    tap.execute("PRAGMA busy_timeout=100")
    with repo.transaction(conn):                       # ingest-style transaction held open
        add_event(conn, aid, kind="portal_status", setter="portal")
        with pytest.raises(repo.BusyError):
            with repo.transaction(tap):
                add_event(tap, aid, kind="stage", setter="kid")
    assert [e["kind"] for e in repo.events_for(conn, aid)] == ["portal_status"]


def test_tap_waits_for_ingest_commit_and_history_stays_ordered(db_path, conn):
    aid = add_assignment(conn)
    tap = schema.connect(db_path)                      # default 5000 ms busy timeout
    holding, errors = threading.Event(), []

    def ingest():
        ing = schema.connect(db_path)                   # sqlite connections are per-thread
        try:
            with repo.transaction(ing):
                add_event(ing, aid, kind="portal_status", setter="portal")
                holding.set()
                time.sleep(0.4)
        except Exception as exc:                        # pragma: no cover - reported below
            errors.append(exc)

    t = threading.Thread(target=ingest)
    t.start()
    assert holding.wait(5)
    try:
        with repo.transaction(tap):
            add_event(tap, aid, kind="stage", setter="kid")
        outcome = "committed"
    except repo.BusyError:
        outcome = "busy"
    t.join()
    kinds = [e["kind"] for e in repo.events_for(conn, aid)]
    assert not errors and kinds == (["portal_status", "stage"] if outcome == "committed" else ["portal_status"])


def test_waiting_tap_commits_rather_than_failing_within_default_timeout(db_path, conn):
    aid = add_assignment(conn)
    tap = schema.connect(db_path)
    holding = threading.Event()

    def ingest():
        ing = schema.connect(db_path)
        with repo.transaction(ing):
            add_event(ing, aid, kind="portal_status", setter="portal")
            holding.set()
            time.sleep(0.3)

    t = threading.Thread(target=ingest); t.start(); holding.wait(5)
    with repo.transaction(tap):
        add_event(tap, aid, kind="stage", setter="kid")
    t.join()
    assert [e["kind"] for e in repo.events_for(conn, aid)] == ["portal_status", "stage"]
