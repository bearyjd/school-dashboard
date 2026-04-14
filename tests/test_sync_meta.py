"""Tests for school_dashboard/sync_meta.py"""
from datetime import datetime, timezone

from school_dashboard.sync_meta import read_sync_meta, write_sync_source


def test_read_missing_file_returns_empty(tmp_path):
    result = read_sync_meta(str(tmp_path / "nonexistent.json"))
    assert result == {}


def test_read_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "meta.json"
    p.write_text("not valid json")
    assert read_sync_meta(str(p)) == {}


def test_write_creates_file_with_source(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("ixl", "ok", path=p)
    meta = read_sync_meta(p)
    assert meta["ixl"]["last_result"] == "ok"
    assert "last_run" in meta["ixl"]


def test_write_updates_existing_source(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("ixl", "ok", path=p)
    write_sync_source("ixl", "error", path=p)
    meta = read_sync_meta(p)
    assert meta["ixl"]["last_result"] == "error"


def test_write_preserves_other_sources(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("ixl", "ok", path=p)
    write_sync_source("sgy", "ok", path=p)
    meta = read_sync_meta(p)
    assert "ixl" in meta
    assert "sgy" in meta


def test_write_creates_parent_dirs(tmp_path):
    p = str(tmp_path / "nested" / "dir" / "meta.json")
    write_sync_source("gc", "ok", path=p)
    meta = read_sync_meta(p)
    assert meta["gc"]["last_result"] == "ok"


def test_last_run_is_utc_iso_timestamp(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("ixl", "ok", path=p)
    meta = read_sync_meta(p)
    ts = meta["ixl"]["last_run"]
    dt = datetime.fromisoformat(ts)
    assert dt.tzinfo is not None, "timestamp must be timezone-aware"
    assert dt.tzinfo == timezone.utc


def test_read_uses_env_var_path(tmp_path, monkeypatch):
    p = str(tmp_path / "meta.json")
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", p)
    write_sync_source("ixl", "ok")  # no explicit path — uses env var
    meta = read_sync_meta()  # no explicit path — uses env var
    assert meta["ixl"]["last_result"] == "ok"
