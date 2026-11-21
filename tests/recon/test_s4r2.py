"""S4 review round 2: URL credential filter, merge force pin, debounce visibility, config, NotFound."""
import logging
from collections import Counter
from datetime import timedelta

import pytest

from recon.api_support import TOKEN, data
from recon.report_support import ps, sgy
from recon.s4_support import NOW, open_conn
from recon.test_api_v2_validate import A, code
from school_dashboard.assignments import readmodel
from school_dashboard.assignments.health import IngestHealth
from web import api_v2_validate as v

BASE = "https://portal.example.com/x"


# ---- 1. safe_url ----
@pytest.mark.parametrize("suffix", [
    "?authorization=abc", "?jwt=abc", "?bearer=abc", "?token[]=abc", "?token%5B%5D=abc", "?token[0]=abc",
    "?a=1&amp;token=abc", "?code=abc", "?otp=1", "?passcode=1", "?auth_code=1", "?Authorization=abc",
    "?%74oken=abc", "#access_token=abc", "/f;jwt=abc", "?api%5Fkey=abc", "?x=1&amp;code=2"])
def test_credential_urls_are_dropped(suffix):
    assert readmodel.safe_url(BASE + suffix) is None


@pytest.mark.parametrize("suffix", [
    "?author=bob", "?assignment_id=4", "?monkey=1", "?encode=utf8", "?barcode=123", "?zipcode=12345",
    "?a=1&amp;page=2", "?ids[]=1", "?decode=1", "?passage=2"])
def test_plain_urls_survive(suffix):
    assert readmodel.safe_url(BASE + suffix) == BASE + suffix


# ---- 2. merge force pin ----
@pytest.mark.parametrize("bad", [1, "true", None, 0, "false", 1.0])
def test_merge_force_must_be_bool(bad):
    assert code(v.parse_merge, {"into": 2, "actor": A, "force": bad}) == "invalid_field"


@pytest.mark.parametrize("ok", [True, False])
def test_merge_force_bool_accepted(ok):
    assert v.parse_merge({"into": 2, "actor": A, "force": ok}).force is ok


# ---- 3. debounce visibility ----
def _ctx(c, now):
    from school_dashboard.assignments import ingest_kid
    return ingest_kid.build_context(c, "Alex", now=now, no_school_rows=[], baseline=False, file_hashes={})


def _rows(c):
    return c.execute("SELECT status, reason, last_error FROM push_outbox WHERE assignment_id = 1"
                     " AND flag = 'disputed' ORDER BY id").fetchall()


def test_debounced_push_is_counted_and_does_not_extend_window(tmp_path):
    from school_dashboard.assignments import ingest_status
    c = open_conn(tmp_path, [sgy("Essay", "Math 6", "s1")])
    counts = Counter()
    for minutes in (0, 60, 120, 300):
        ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(minutes=minutes)), 1, "disputed", counts)
    assert counts["pushes_enqueued"] == 1 and counts["pushes_debounced"] == 3
    assert [r["status"] for r in _rows(c)].count("pending") == 1
    assert [(r["status"], r["last_error"]) for r in _rows(c)][1:] == [("suppressed", "debounced")] * 3
    ingest_status.enqueue_debounced(c, _ctx(c, NOW + timedelta(hours=6, minutes=1)), 1, "disputed", counts)
    assert counts["pushes_enqueued"] == 2
    assert [r["status"] for r in _rows(c)].count("pending") == 2


def test_only_debounced_rows_do_not_start_a_window(tmp_path):
    from school_dashboard.assignments import repo
    c = open_conn(tmp_path, [sgy("Essay", "Math 6", "s1")])
    repo.enqueue_push(c, 1, "disputed", NOW, "transition")
    c.execute("UPDATE push_outbox SET status = 'suppressed', last_error = 'debounced'")
    assert not repo.recent_push_exists(c, 1, "disputed", NOW - timedelta(hours=1))
    c.execute("UPDATE push_outbox SET last_error = 'expired'")
    assert repo.recent_push_exists(c, 1, "disputed", NOW - timedelta(hours=1))


def test_kid_result_carries_debounced_and_report_shows_it(tmp_path):
    from school_dashboard.assignments import repo, report, report_render
    from school_dashboard.assignments.ingest_types import KidResult
    assert KidResult(kid="Alex", ok=True).pushes_debounced == 0
    c = open_conn(tmp_path, [sgy("Essay", "Math 6", "s1")])
    repo.enqueue_push(c, 1, "disputed", NOW, "transition")
    c.execute("INSERT INTO push_outbox (assignment_id, flag, occurrence_at, reason, status, created_at, last_error)"
              " VALUES (1, 'disputed', ?, 'transition', 'suppressed', ?, 'debounced')",
              ((NOW + timedelta(hours=1)).isoformat(),) * 2)
    old = (NOW - timedelta(days=3)).isoformat()
    c.execute("INSERT INTO push_outbox (assignment_id, flag, occurrence_at, reason, status, created_at, last_error)"
              " VALUES (1, 'disputed', ?, 'transition', 'suppressed', ?, 'debounced')", (old, old))
    inp = report.read_input(c, now=NOW + timedelta(hours=2))
    assert inp.debounced == 1 and inp.outbox == {"pending": 1}
    rep = report.build_report(inp, IngestHealth(True, None, None))
    assert rep["debounced_last_24h"] == 1
    assert "debounced_last_24h: 1" in report_render.render_text(rep)


def test_run_logs_one_info_line_with_debounced_count(tmp_path, caplog):
    from recon.ingest_support import make_db
    from school_dashboard.assignments import ingest
    db = make_db(tmp_path, {"seed": []})
    with caplog.at_level(logging.INFO, logger="school_dashboard.assignments.ingest"):
        ingest.run(db, [], file_hashes={}, now=NOW, no_school_rows=[])
    lines = [r.getMessage() for r in caplog.records if "recon ingest:" in r.getMessage()]
    assert len(lines) == 1 and "0 debounced pushes" in lines[0]


# ---- 4. config ----
def test_config_never_exposes_the_token(api_factory):
    api = api_factory([])
    r = api.get("/api/v2/config")
    assert TOKEN not in r.get_data(as_text=True)
    assert data(r)["data"]["write_auth"] == "token"


# ---- 5. NotFound instead of assert ----
def test_settle_raises_not_found_for_missing_target(tmp_path):
    from school_dashboard.assignments import service_split
    from school_dashboard.assignments.service_types import NotFound, Who
    c = open_conn(tmp_path, [sgy("Essay", "Math 6", "s1")])
    with pytest.raises(NotFound):
        service_split._settle(c, 999, 1, 1, 1, Who("parent", "Dana"), NOW)
