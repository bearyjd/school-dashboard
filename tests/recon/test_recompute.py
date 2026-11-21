"""recompute(): flags only, no scrape; the clock moves and flags follow (event logged)."""
from recon.ingest_support import WED, build_results, make_db
from recon.support import local

FRI = local(2026, 11, 20, 17)
SAT = local(2026, 11, 21, 10)
SGY_DUE_FRI = {"Alex": [{"title": "Report", "course": "Math 6", "due": "2026-11-20", "link": "https://sgy.example.invalid/r/1"}]}


def _ingest(db_path, now, sgy):
    from school_dashboard.assignments import ingest
    results, hashes = build_results({"sgy": sgy, "hash": {"schoology": "s1"}})
    return ingest.run(db_path, results, file_hashes=hashes, now=now, no_school_rows=[])


def _recompute(db_path, now, rows=(), kid="Alex"):
    from school_dashboard.assignments import ingest, schema
    conn = schema.connect(db_path)
    try:
        return ingest.recompute(conn, kid, now=now, no_school_rows=list(rows))
    finally:
        conn.close()


def _rows(db_path, sql, args=()):
    from school_dashboard.assignments import schema
    conn = schema.connect(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def test_friday_to_saturday_drops_do_tonight_and_logs_a_flag_event(tmp_path):
    db_path = make_db(tmp_path)
    _ingest(db_path, FRI, SGY_DUE_FRI)
    assert _rows(db_path, "SELECT flag FROM assignments")[0]["flag"] == "do_tonight"
    res = _recompute(db_path, SAT)
    assert res.ok and res.flag_changes == 1 and res.kid == "Alex"
    assert _rows(db_path, "SELECT flag FROM assignments")[0]["flag"] == "missing"
    events = _rows(db_path, "SELECT * FROM assignment_events WHERE kind = 'flag' ORDER BY id")
    assert (events[-1]["from_value"], events[-1]["to_value"]) == ("do_tonight", "missing")
    assert _rows(db_path, "SELECT COUNT(*) AS n FROM push_outbox")[0]["n"] == 0  # date-only overdue


def test_recompute_is_idempotent_and_does_not_touch_links(tmp_path):
    db_path = make_db(tmp_path)
    _ingest(db_path, FRI, SGY_DUE_FRI)
    links_before = _rows(db_path, "SELECT * FROM source_links")
    _recompute(db_path, SAT)
    events = _rows(db_path, "SELECT COUNT(*) AS n FROM assignment_events")[0]["n"]
    again = _recompute(db_path, SAT)
    assert again.flag_changes == 0
    assert _rows(db_path, "SELECT COUNT(*) AS n FROM assignment_events")[0]["n"] == events
    assert _rows(db_path, "SELECT * FROM source_links") == links_before


def test_unknown_kid_is_a_clean_no_op(tmp_path):
    db_path = make_db(tmp_path)
    res = _recompute(db_path, WED, kid="Zed")
    assert res.ok and res.flag_changes == 0 and res.error is None


def test_no_school_rows_shift_do_tonight(tmp_path):
    db_path = make_db(tmp_path)
    sgy = {"Alex": [{"title": "Report", "course": "Math 6", "due": "2026-11-24", "link": "https://sgy.example.invalid/r/2"}]}
    _ingest(db_path, FRI, sgy)
    assert _rows(db_path, "SELECT flag FROM assignments")[0]["flag"] == "upcoming"
    _recompute(db_path, FRI, rows=[{"date": "2026-11-23", "type": "NO_SCHOOL", "child": None}])
    assert _rows(db_path, "SELECT flag FROM assignments")[0]["flag"] == "do_tonight"


def test_grace_comes_from_class_settings(tmp_path):
    seed = {"kid": "Alex", "class_name": "Math 6", "class_key": "math 6", "title": "Packet", "title_key": "packet",
            "due_date": None, "stage": 2, "stage_since": "2026-11-16T12:00:00-05:00"}
    db_path = make_db(tmp_path, {"seed": [seed]})
    _recompute(db_path, WED)
    assert _rows(db_path, "SELECT flag FROM assignments")[0]["flag"] == "waiting_on_teacher"
    from school_dashboard.assignments import schema
    conn = schema.connect(db_path)
    conn.execute("INSERT INTO class_settings (kid, class_key, grace_school_days) VALUES ('Alex', 'math 6', 0)")
    conn.close()
    _recompute(db_path, WED)
    assert _rows(db_path, "SELECT flag FROM assignments")[0]["flag"] == "done_not_received"
