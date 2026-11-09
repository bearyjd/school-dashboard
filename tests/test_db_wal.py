"""SQLite WAL + busy timeout: migration, read-only readers, consistent backups."""
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from school_dashboard import backup, db, maintenance, paths, school_calendar

ROOT = Path(__file__).resolve().parent.parent


def _mode(path) -> str:
    with closing(sqlite3.connect(str(path))) as c:
        return c.execute("PRAGMA journal_mode").fetchone()[0]


def test_init_db_sets_wal_persistently(tmp_path):
    p = tmp_path / "s.db"
    db.init_db(str(p))
    assert _mode(p) == "wal"


def test_connection_has_busy_timeout_and_wal(tmp_path):
    conn = db._connect(str(tmp_path / "s.db"))
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_existing_delete_mode_db_migrates_on_first_connect(tmp_path):
    p = tmp_path / "old.db"
    with closing(sqlite3.connect(str(p))) as c:
        c.execute("CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, child TEXT NOT NULL, "
                  "title TEXT NOT NULL, due_date TEXT, type TEXT NOT NULL DEFAULT 'assignment', "
                  "source TEXT NOT NULL DEFAULT 'manual', completed INTEGER NOT NULL DEFAULT 0, "
                  "completed_at TEXT, notes TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))")
        c.execute("INSERT INTO items (child, title) VALUES ('Ford', 'keep me')")
        c.commit()
    assert _mode(p) == "delete"
    db.init_db(str(p))
    assert _mode(p) == "wal"
    assert [i["title"] for i in db.list_items(str(p))] == ["keep me"]


def test_every_non_readonly_connect_goes_through_db_helper():
    """Guard: raw sqlite3.connect is allowed only for mode=ro URIs and in db.py/backup.py."""
    offenders = []
    for f in [*(ROOT / "school_dashboard").glob("*.py"), ROOT / "web" / "app.py"]:
        if f.name in ("db.py", "backup.py"):
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"sqlite3\.connect\(", line) and "mode=ro" not in line:
                offenders.append(f"{f.name}:{n}")
    assert offenders == []


def test_readonly_uri_reader_sees_uncheckpointed_wal_rows(tmp_path):
    p = tmp_path / "s.db"
    db.init_db(str(p))
    writer = db._connect(str(p))  # held open: rows live in -wal, not yet in the main file
    try:
        with writer:
            writer.execute("INSERT INTO events (date, title, type, child, source) "
                           "VALUES ('2026-09-01','Quiz','QUIZ','Ford','calendar:x')")
        assert Path(str(p) + "-wal").exists()
        assert school_calendar._preview_existing(p, "calendar:x", []) == 1
    finally:
        writer.close()


def test_watchdog_latest_digests_reads_wal_db_with_and_without_open_writer(tmp_path):
    from school_dashboard import watchdog
    p = tmp_path / "s.db"
    db.init_db(str(p))
    writer = db._connect(str(p))
    try:
        db.create_digest(str(p), "Morning Briefing", [{"t": 1}])
        assert Path(str(p) + "-wal").exists()
        assert "Morning Briefing" in watchdog.latest_digests(p)
    finally:
        writer.close()
    assert "Morning Briefing" in watchdog.latest_digests(p)


def test_readonly_uri_reader_works_after_clean_close(tmp_path):
    p = tmp_path / "s.db"
    db.init_db(str(p))
    db.create_item(str(p), child="Ford", title="x")
    with closing(sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)) as c:
        assert c.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_backup_of_wal_db_with_open_writer_is_consistent(state_env):
    dbp = paths.db_path()
    db.init_db(str(dbp))
    db.create_item(str(dbp), child="Ford", title="in the wal")
    writer = db._connect(str(dbp))
    try:
        assert Path(str(dbp) + "-wal").exists()
        maintenance.enable("test")
        dest = backup.create_backup()
    finally:
        writer.close()
    copy = dest / "school.db"
    assert not Path(str(copy) + "-wal").exists()
    with closing(sqlite3.connect(str(copy))) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert c.execute("SELECT title FROM items").fetchall() == [("in the wal",)]
