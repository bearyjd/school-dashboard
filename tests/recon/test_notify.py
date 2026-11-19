"""Outbox sender: off by default, baseline-silent, one push per kid, retry, expiry, attempts cap."""
import logging
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from recon.ingest_support import WED, make_db
from recon.report_support import sgy

TOPIC = "synthetic-topic-7f3a91"


@pytest.fixture
def env(state_env, monkeypatch):
    monkeypatch.setenv("RECON_PUSH_ENABLED", "1")
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)
    return state_env


def _setup(state_dir, *, baseline=True):
    from school_dashboard.assignments import repo, schema
    seed = [sgy("Essay Draft", "Language Arts", "s1"), sgy("Volcano Poster", "Art", "s2"),
            {**sgy("Map Test", "Social Studies", "s3"), "kid": "Sam"}]
    path = make_db(state_dir, {"seed": seed}, baseline=baseline)
    conn = schema.connect(path)
    ids = [r[0] for r in conn.execute("SELECT id FROM assignments ORDER BY id")]
    with repo.transaction(conn):
        repo.enqueue_push(conn, ids[0], "disputed", WED, "transition")
        repo.enqueue_push(conn, ids[1], "missing", WED, "transition")
        repo.enqueue_push(conn, ids[2], "missing", WED, "transition")
    return conn, ids


def _status(conn):
    return [(r["status"], r["attempts"], r["last_error"]) for r in conn.execute(
        "SELECT status, attempts, last_error FROM push_outbox ORDER BY id")]


def _send_pending(conn, send, now=WED + timedelta(minutes=5)):
    from school_dashboard.assignments import notify
    return notify.send_pending(conn, now=now, send=send)


def test_flag_off_leaves_rows_untouched_and_never_sends(env, monkeypatch):
    monkeypatch.delenv("RECON_PUSH_ENABLED")
    conn, _ = _setup(env)
    send = MagicMock()
    _send_pending(conn, send)
    send.assert_not_called()
    assert [s[0] for s in _status(conn)] == ["pending"] * 3


def test_no_baseline_leaves_rows_untouched(env):
    conn, _ = _setup(env, baseline=False)
    send = MagicMock()
    _send_pending(conn, send)
    send.assert_not_called()
    assert [s[0] for s in _status(conn)] == ["pending"] * 3


def test_maintenance_skips(env):
    from school_dashboard import maintenance
    conn, _ = _setup(env)
    maintenance.enable("test")
    send = MagicMock()
    _send_pending(conn, send)
    send.assert_not_called()
    assert [s[0] for s in _status(conn)] == ["pending"] * 3


def test_one_push_per_kid_with_all_that_kids_rows(env):
    conn, _ = _setup(env)
    send = MagicMock()
    report = _send_pending(conn, send)
    assert send.call_count == 2
    by_body = {c.args[1]: c.args for c in send.call_args_list}
    alex = next(a for body, a in by_body.items() if "Alex:" in body)
    assert alex[0] == TOPIC and alex[2] == "School: needs action"
    assert alex[1].splitlines() == ["Alex: Essay Draft — Disputed (Language Arts)",
                                    "Alex: Volcano Poster — Missing (Art)"]
    assert any(b == "Sam: Map Test — Missing (Social Studies)" for b in by_body)
    assert [s[0] for s in _status(conn)] == ["sent"] * 3
    assert report.sent == 3 and report.failed == 0
    assert conn.execute("SELECT COUNT(*) FROM push_outbox WHERE sent_at IS NOT NULL").fetchone()[0] == 3


def test_exception_marks_failed_with_type_name_then_retry_sends_once(env, caplog):
    conn, _ = _setup(env)
    caplog.set_level(logging.DEBUG)

    def boom(topic, body, title):
        raise ConnectionError(f"secret {topic} {body}")

    _send_pending(conn, boom)
    assert _status(conn) == [("failed", 1, "ConnectionError")] * 3
    assert TOPIC not in caplog.text and "Essay" not in caplog.text and "secret" not in caplog.text
    send = MagicMock()
    _send_pending(conn, send, now=WED + timedelta(minutes=30))
    assert send.call_count == 2
    assert [s[0] for s in _status(conn)] == ["sent"] * 3
    _send_pending(conn, send, now=WED + timedelta(minutes=60))
    assert send.call_count == 2


def test_rows_older_than_max_age_are_suppressed_as_expired_and_never_sent(env):
    from school_dashboard.assignments import notify
    conn, _ = _setup(env)
    send = MagicMock()
    report = _send_pending(conn, send, now=WED + notify.MAX_PENDING_AGE + timedelta(minutes=1))
    send.assert_not_called()
    assert _status(conn) == [("suppressed", 0, "expired")] * 3
    assert report.suppressed == 3


def test_max_attempts_reached_stays_failed_and_is_not_retried(env):
    from school_dashboard.assignments import notify
    conn, _ = _setup(env)
    conn.execute("UPDATE push_outbox SET status = 'failed', attempts = ?, last_error = 'OSError'",
                 (notify.MAX_ATTEMPTS,))
    send = MagicMock()
    report = _send_pending(conn, send)
    send.assert_not_called()
    assert [s[0] for s in _status(conn)] == ["failed"] * 3
    assert report.exhausted == 3


def test_one_kid_failing_does_not_block_another(env):
    conn, _ = _setup(env)

    def flaky(topic, body, title):
        if body.startswith("Alex"):
            raise TimeoutError("x")

    _send_pending(conn, flaky)
    got = {r["kid"]: r["status"] for r in conn.execute(
        "SELECT a.kid, o.status FROM push_outbox o JOIN assignments a ON a.id = o.assignment_id")}
    assert got == {"Alex": "failed", "Sam": "sent"}


def test_missing_topic_keeps_rows_pending_logs_one_error_and_does_not_raise(env, monkeypatch, caplog):
    monkeypatch.delenv("NTFY_TOPIC")
    conn, _ = _setup(env)
    caplog.set_level(logging.ERROR)
    send = MagicMock()
    _send_pending(conn, send)
    send.assert_not_called()
    assert [s[0] for s in _status(conn)] == ["pending"] * 3
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1


def test_default_sender_posts_high_priority_with_timeout_and_raise_for_status(env):
    from school_dashboard.digest import DASHBOARD_BASE
    conn, _ = _setup(env)
    resp = MagicMock()
    with patch("requests.post", return_value=resp) as post:
        _send_pending(conn, None)
    assert post.call_count == 2
    url = post.call_args.args[0]
    assert TOPIC in url and url.startswith("https://ntfy.sh/")
    headers = post.call_args.kwargs["headers"]
    assert headers["Priority"] == "high" and headers["Click"] == DASHBOARD_BASE
    assert headers["Title"] == "School: needs action"
    assert post.call_args.kwargs["timeout"] > 0
    assert resp.raise_for_status.call_count == 2
    assert [s[0] for s in _status(conn)] == ["sent"] * 3


def test_default_sender_http_error_marks_failed_without_logging_the_topic(env, caplog):
    import requests
    conn, _ = _setup(env)
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.HTTPError(f"500 for url https://ntfy.sh/{TOPIC}")
    caplog.set_level(logging.DEBUG)
    with patch("requests.post", return_value=resp):
        _send_pending(conn, None)
    assert _status(conn) == [("failed", 1, "HTTPError")] * 3
    assert TOPIC not in caplog.text


def test_run_shadow_default_after_ingest_is_the_sender_and_a_noop_when_off(env, monkeypatch):
    from recon.ingest_support import ns
    from school_dashboard.assignments import ingest
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    conn, _ = _setup(env)
    inputs = env.parent / "inputs"
    (inputs / "ixl").mkdir(parents=True)
    args = ns(sgy_file=str(inputs / "sgy.json"), ps_file=str(inputs / "ps.json"), ixl_dir=str(inputs / "ixl"),
              state_file=str(env / "school-state.json"))
    monkeypatch.delenv("RECON_PUSH_ENABLED")
    with patch("requests.post") as post:
        assert ingest.run_shadow(args, now=WED) is not None
    post.assert_not_called()
    assert [s[0] for s in _status(conn)] == ["pending"] * 3
    monkeypatch.setenv("RECON_PUSH_ENABLED", "1")
    with patch("requests.post") as post:
        assert ingest.run_shadow(args, now=WED + timedelta(minutes=1)) is not None
    assert post.call_count == 2
    assert [s[0] for s in _status(conn)] == ["sent"] * 3
