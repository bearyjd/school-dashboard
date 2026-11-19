"""S4 review round: push debounce (S2a) and pins for surviving mutations (C6)."""
import json
import sqlite3
from collections import Counter
from datetime import timedelta

import pytest

from recon.report_support import ps, sgy
from recon.s4_support import NOW, open_conn
from school_dashboard.assignments import repo


def two(tmp_path):
    return open_conn(tmp_path, [sgy("Essay", "Math 6", "s1"), ps("Essay", "Math 6", "p1")])


# ---- S2a debounce ----
def _ctx(c, now, baseline=False):
    from school_dashboard.assignments import ingest_kid
    return ingest_kid.build_context(c, "Alex", now=now, no_school_rows=[], baseline=baseline, file_hashes={})


def _rows(c, flag="missing"):
    return c.execute("SELECT status, reason FROM push_outbox WHERE assignment_id = 1 AND flag = ?", (flag,)).fetchall()


def test_enqueue_is_debounced_for_six_hours(tmp_path):
    from school_dashboard.assignments import ingest_status
    c = two(tmp_path)
    counts = Counter()
    for h in range(6):
        ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=h)), 1, "missing", counts)
    assert len(_rows(c)) == 1 and counts["pushes_enqueued"] == 1
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=6, minutes=1)), 1, "missing", counts)
    assert len(_rows(c)) == 2 and counts["pushes_enqueued"] == 2
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=1)), 1, "disputed", counts)
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=1)), 2, "missing", counts)
    assert len(_rows(c, "disputed")) == 1
    assert c.execute("SELECT COUNT(*) FROM push_outbox WHERE assignment_id = 2").fetchone()[0] == 1


@pytest.mark.parametrize("status", ["sent", "failed", "pending"])
def test_any_recent_status_debounces(tmp_path, status):
    from school_dashboard.assignments import ingest_status
    c = two(tmp_path)
    repo.enqueue_push(c, 1, "missing", NOW, "transition")
    c.execute("UPDATE push_outbox SET status = ?", (status,))
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=2)), 1, "missing", Counter())
    assert len(_rows(c)) == 1


def test_baseline_suppressed_rows_do_not_debounce(tmp_path):
    from school_dashboard.assignments import ingest_status
    c = two(tmp_path)
    ingest_status.enqueue_debounced(c, _ctx(c, NOW, baseline=True), 1, "missing", Counter())
    assert [tuple(r) for r in _rows(c)] == [("suppressed", "baseline")]
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=1)), 1, "missing", Counter())
    assert [tuple(r) for r in _rows(c)] == [("suppressed", "baseline"), ("pending", "transition")]


def test_debounce_compares_across_a_dst_offset_change(tmp_path):
    from school_dashboard.assignments import ingest_status
    c = two(tmp_path)
    t0 = NOW.replace(month=11, day=1, hour=0)  # EDT (-04:00)
    repo.enqueue_push(c, 1, "missing", t0, "transition")
    later = (t0 + timedelta(hours=7)).astimezone(NOW.tzinfo)
    ingest_status.enqueue_debounced(c, _ctx(c, later), 1, "missing", Counter())
    assert len(_rows(c)) == 2


# ---- C6 pins ----
def test_assignments_and_links_cannot_be_deleted(tmp_path):
    c = two(tmp_path)
    for sql in ("DELETE FROM assignments WHERE id = 1", "DELETE FROM source_links"):
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql)
    assert c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 2


def _rec(raw):
    from recon.support import mk_record
    return mk_record(raw=raw)


@pytest.mark.parametrize("size,kept", [(8191, True), (8192, True), (8193, False)])
def test_raw_json_8_kib_boundary(size, kept):
    from school_dashboard.assignments import ingest_kid
    overhead = len(json.dumps({"a": ""}))
    out = ingest_kid._raw_json(_rec({"a": "x" * (size - overhead)}))
    assert len(json.dumps({"a": "x" * (size - overhead)})) == size
    assert (json.loads(out).get("truncated") is None) is kept
    if not kept:
        assert json.loads(out) == {"truncated": True, "bytes": size}


def _big(i, title="\u20ac" * 200):
    return {"id": i, "kid": "\u20ac" * 5, "title": title, "class_name": None, "flag": "missing"}


def _size(row):
    from school_dashboard.assignments import notify
    return len(notify._line(row).encode("utf-8")) + 1


@pytest.mark.parametrize("filler", ["\u20ac", "\u00e9", "e"])
def test_push_body_never_exceeds_the_byte_bound_and_is_maximal(filler):
    from school_dashboard.assignments import notify
    reserve = len("\n+99999 more")
    hit_limit = 0
    for k in range(1, 110):
        rows = [_big(i) for i in range(9)] + [_big(9, filler * k)]
        chosen, msg = notify._batch(rows)
        assert len(msg.encode("utf-8")) <= notify.MAX_BODY_BYTES
        if len(chosen) < len(rows):
            used = sum(_size(r) for r in chosen)
            assert used + _size(rows[len(chosen)]) + reserve > notify.MAX_BODY_BYTES  # one more would not fit
            assert msg.splitlines()[-1] == f"+{len(rows) - len(chosen)} more"
            hit_limit += 1
    assert notify._MORE_RESERVE == reserve and (hit_limit > 0 or filler != "\u20ac")


def test_a_row_exactly_at_the_limit_is_taken():
    from school_dashboard.assignments import notify
    rows = [_big(i) for i in range(9)]
    room = notify.MAX_BODY_BYTES - notify._MORE_RESERVE - sum(_size(r) for r in rows)
    extra = room - _size(_big(9, ""))
    title = "\u20ac" * (extra // 3) + ("", "e", "\u00e9")[extra % 3]
    last = _big(9, title)
    assert _size(last) == room
    chosen, _ = notify._batch([*rows, last])
    assert len(chosen) == 10
    chosen, msg = notify._batch([*rows, _big(9, title + "e")])
    assert len(chosen) == 9 and msg.endswith("+1 more")
