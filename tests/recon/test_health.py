from datetime import datetime, timedelta, timezone

import pytest
from recon.support import TZ, local

from school_dashboard.assignments import health

NOW = local(2026, 11, 18, 17, 0)  # Wednesday; last scheduled sync 14:30


def _entry(ok: datetime, result: str = "ok") -> dict:
    stamp = ok.astimezone(timezone.utc).isoformat(timespec="seconds")
    return {"last_run": stamp, "last_result": result, "last_ok": stamp}


def test_constants():
    assert health.INGEST_SOURCE == "recon_ingest"
    assert health.SYNC_NAME == {"schoology": "sgy", "powerschool": "powerschool", "ixl": "ixl"}


def test_ok_entry_reports_data_as_of_from_last_ok():
    ok = local(2026, 11, 18, 14, 35)
    got = health.ingest_health({"recon_ingest": _entry(ok)}, now=NOW, tz=TZ)
    assert got == health.IngestHealth(ok=True, data_as_of=ok.astimezone(timezone.utc), issue=None)


def test_missing_entry_is_not_ok():
    got = health.ingest_health({}, now=NOW, tz=TZ)
    assert (got.ok, got.data_as_of) == (False, None) and got.issue


def test_error_result_is_not_ok_but_keeps_data_as_of():
    ok = local(2026, 11, 18, 14, 35)
    entry = {**_entry(ok), "last_result": "error"}
    got = health.ingest_health({"recon_ingest": entry}, now=NOW, tz=TZ)
    assert got.ok is False and got.issue and got.data_as_of == ok.astimezone(timezone.utc)


def test_stale_by_watchdog_rule_is_not_ok():
    old = local(2026, 11, 16, 14, 35)
    got = health.ingest_health({"recon_ingest": _entry(old)}, now=NOW, tz=TZ)
    assert got.ok is False and got.issue == "stale"
    assert got.data_as_of == old.astimezone(timezone.utc)


def test_entry_without_any_ok_time_is_not_ok():
    got = health.ingest_health({"recon_ingest": {"last_run": "2026-11-18T19:00:00+00:00",
                                                 "last_result": "paused"}}, now=NOW, tz=TZ)
    assert got.ok is False and got.data_as_of is None


def test_garbage_entry_never_raises():
    for bad in (None, 5, "x", [], {"last_ok": 12}):
        got = health.ingest_health({"recon_ingest": bad}, now=NOW, tz=TZ)
        assert got.ok is False


def test_failed_run_does_not_move_data_as_of():
    ok = local(2026, 11, 18, 14, 35)
    good = health.ingest_health({"recon_ingest": _entry(ok)}, now=NOW, tz=TZ)
    failed = health.ingest_health({"recon_ingest": {**_entry(ok), "last_result": "error",
                                                    "last_run": "2026-11-18T22:00:00+00:00"}},
                                  now=NOW, tz=TZ)
    assert failed.data_as_of == good.data_as_of


def test_stale_sources_maps_sync_names_to_link_sources():
    fresh = _entry(local(2026, 11, 18, 14, 35))
    old = _entry(local(2026, 11, 16, 14, 35))
    meta = {"sgy": old, "powerschool": fresh, "ixl": old, "gc": old}
    assert health.stale_sources(meta, now=NOW, tz=TZ) == frozenset({"schoology", "ixl"})


def test_stale_sources_none_when_fresh():
    fresh = _entry(local(2026, 11, 18, 14, 35))
    meta = {"sgy": fresh, "powerschool": fresh, "ixl": fresh}
    assert health.stale_sources(meta, now=NOW, tz=TZ) == frozenset()


def test_stale_sources_never_ok_needs_first_seen():
    assert health.stale_sources({}, now=NOW, tz=TZ) == frozenset()
    seen = {"sgy": NOW - timedelta(days=3)}
    got = health.stale_sources({}, now=NOW, tz=TZ, first_seen=seen)
    assert got == frozenset({"schoology"})


def test_ingest_first_seen_makes_missing_entry_never_ok():
    got = health.ingest_health({}, now=NOW, tz=TZ, first_seen=NOW - timedelta(days=3))
    assert got.ok is False and got.issue == "never_ok"
