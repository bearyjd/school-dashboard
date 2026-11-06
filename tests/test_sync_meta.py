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


def test_last_ok_set_only_on_ok(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("ixl", "ok", path=p)
    first_ok = read_sync_meta(p)["ixl"]["last_ok"]
    write_sync_source("ixl", "error", path=p)
    entry = read_sync_meta(p)["ixl"]
    assert entry["last_result"] == "error"
    assert entry["last_ok"] == first_ok


def test_last_ok_absent_when_never_ok(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("gc", "error", path=p)
    assert "last_ok" not in read_sync_meta(p)["gc"]


def test_pause_keeps_last_ok(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("gc", "ok", path=p)
    ok = read_sync_meta(p)["gc"]["last_ok"]
    write_sync_source("gc", "paused", path=p)
    assert read_sync_meta(p)["gc"]["last_ok"] == ok


def test_last_ok_time_legacy_fallback():
    from school_dashboard.sync_meta import last_ok_time
    ts = "2026-01-01T00:00:00+00:00"
    assert last_ok_time({"last_run": ts, "last_result": "ok"}) == ts
    assert last_ok_time({"last_run": ts, "last_result": "error"}) is None
    assert last_ok_time(None) is None


def test_error_over_legacy_ok_entry_keeps_its_run_as_last_ok(tmp_path):
    import json
    p = tmp_path / "meta.json"
    ts = "2026-01-01T00:00:00+00:00"
    p.write_text(json.dumps({"ixl": {"last_run": ts, "last_result": "ok"}}))
    write_sync_source("ixl", "error", path=str(p))
    assert read_sync_meta(str(p))["ixl"]["last_ok"] == ts
