"""No-LLM fallback digest and ntfy delivery-failure handling."""
import argparse
import json
import sqlite3
import logging
from contextlib import closing
from unittest.mock import MagicMock, patch

import pytest
import requests

from school_dashboard import cli, maintenance
from school_dashboard.digest import FALLBACK_NOTE, DigestFallbackSent, deliver_digest, send_ntfy

STATE = {
    "ixl": {"Jack": {"totals": {"Math": {"remaining": 3, "assigned": 5, "done": 2}}}},
    "schoology": {"Jack": {"assignments": []}},
}


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path))
    p = tmp_path / "school-state.json"
    p.write_text(json.dumps(STATE))
    return str(p)


def _ok():
    return MagicMock(ok=True, status_code=200)


def _boom():
    raise RuntimeError("llm down")


# --- send_ntfy raises on failed delivery ---

@patch("school_dashboard.digest.requests.post")
def test_send_ntfy_raises_on_http_error(mock_post, state_file):
    resp = requests.Response()
    resp.status_code = 503
    mock_post.return_value = resp
    with pytest.raises(requests.HTTPError):
        send_ntfy("t", "msg", "Title")


@patch("school_dashboard.digest.requests.post")
def test_send_ntfy_raises_on_timeout(mock_post, state_file):
    mock_post.side_effect = requests.Timeout("slow")
    with pytest.raises(requests.Timeout):
        send_ntfy("t", "msg", "Title")


@patch("school_dashboard.digest.requests.post")
def test_send_ntfy_maintenance_skip_does_not_raise(mock_post, state_file):
    maintenance.enable("t")
    send_ntfy("t", "msg", "Title")
    mock_post.assert_not_called()


# --- deliver_digest ---

@patch("school_dashboard.digest.requests.post")
def test_deliver_digest_success_sends_built_text(mock_post, state_file):
    mock_post.return_value = _ok()
    deliver_digest(lambda: ("AI text", []), topic="t", title="Morning Briefing",
                   state_path=state_file, db_path=None)
    assert mock_post.call_args.kwargs["data"] == b"AI text"
    assert mock_post.call_args.kwargs["headers"]["Title"] == "Morning Briefing"


@patch("school_dashboard.digest.requests.post")
def test_deliver_digest_llm_failure_sends_quick_check_and_raises(mock_post, state_file):
    mock_post.return_value = _ok()
    with pytest.raises(DigestFallbackSent):
        deliver_digest(_boom, topic="t", title="Morning Briefing",
                       state_path=state_file, db_path=None)
    kw = mock_post.call_args.kwargs
    assert kw["headers"]["Title"] == "Morning Briefing (fallback)"
    body = kw["data"].decode()
    assert FALLBACK_NOTE in body
    assert "Jack" in body


@patch("school_dashboard.digest.requests.post")
def test_deliver_digest_fallback_writes_no_digest_row(mock_post, state_file, tmp_path):
    from school_dashboard.db import init_digests_table
    db = str(tmp_path / "school.db")
    init_digests_table(db)
    mock_post.return_value = _ok()
    with pytest.raises(DigestFallbackSent):
        deliver_digest(_boom, topic="t", title="Night Prep", state_path=state_file, db_path=db)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM digests").fetchone()[0] == 0
    assert "AI summary unavailable" in mock_post.call_args.kwargs["data"].decode()


@patch("school_dashboard.digest.requests.post")
def test_deliver_digest_maintenance_does_not_claim_fallback(mock_post, state_file, caplog):
    maintenance.enable("t")
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError) as exc:
        deliver_digest(_boom, topic="t", title="Night Prep", state_path=state_file, db_path=None)
    assert not isinstance(exc.value, DigestFallbackSent)
    assert "fallback sent" not in str(exc.value)
    assert "llm down" in str(exc.value)
    assert "maintenance" in caplog.text.lower()
    mock_post.assert_not_called()


@patch("school_dashboard.digest.requests.post")
def test_deliver_digest_build_failure_logged_with_traceback(mock_post, state_file, caplog):
    mock_post.return_value = _ok()
    with caplog.at_level(logging.ERROR), pytest.raises(DigestFallbackSent):
        deliver_digest(_boom, topic="t", title="Night Prep", state_path=state_file, db_path=None)
    assert any(r.exc_info for r in caplog.records)


@patch("school_dashboard.digest.requests.post")
def test_deliver_digest_fallback_delivery_failure_propagates(mock_post, state_file):
    mock_post.side_effect = requests.ConnectionError("no net")
    with pytest.raises(requests.ConnectionError):
        deliver_digest(_boom, topic="t", title="Night Prep", state_path=state_file, db_path=None)


# --- cmd_digest ---

@pytest.fixture
def digest_env(state_file, monkeypatch):
    monkeypatch.setenv("LITELLM_URL", "http://x/v1")
    monkeypatch.setenv("NTFY_TOPIC", "topic")
    monkeypatch.delenv("GOG_ACCOUNT", raising=False)
    return state_file


@pytest.mark.parametrize("mode,builder,title", [
    ("morning", "build_morning_digest", "Morning Briefing"),
    ("afternoon", "build_afternoon_digest", "Homework Check"),
    ("night", "build_night_digest", "Night Prep"),
])
def test_cmd_digest_llm_failure_sends_fallback_and_exits_nonzero(digest_env, mode, builder, title):
    args = argparse.Namespace(mode=mode, state_file=digest_env)
    with patch(f"school_dashboard.digest.{builder}", side_effect=RuntimeError("llm")), \
         patch("school_dashboard.digest.requests.post", return_value=_ok()) as post:
        with pytest.raises(SystemExit) as exc:
            cli.cmd_digest(args)
    assert exc.value.code == 1
    assert post.call_args.kwargs["headers"]["Title"] == f"{title} (fallback)"


def test_cmd_digest_success_exits_normally(digest_env):
    args = argparse.Namespace(mode="night", state_file=digest_env)
    with patch("school_dashboard.digest.build_night_digest", return_value=("ok", [])), \
         patch("school_dashboard.digest.requests.post", return_value=_ok()) as post:
        cli.cmd_digest(args)
    assert post.call_args.kwargs["headers"]["Title"] == "Night Prep"


def test_cmd_digest_ntfy_failure_propagates(digest_env):
    args = argparse.Namespace(mode="night", state_file=digest_env)
    with patch("school_dashboard.digest.build_night_digest", return_value=("ok", [])), \
         patch("school_dashboard.digest.requests.post", side_effect=requests.Timeout("t")):
        with pytest.raises(requests.Timeout):
            cli.cmd_digest(args)


# --- /api/sync best-effort digest delivery ---

@pytest.mark.parametrize("digest_kind", ["quick", "full"])
def test_api_sync_digest_ntfy_failure_is_logged_not_fatal(
        digest_kind, tmp_path, monkeypatch, state_file, caplog):
    import web.app as app_module
    monkeypatch.setenv("NTFY_TOPIC", "topic")
    monkeypatch.setenv("LITELLM_URL", "http://x/v1")
    monkeypatch.setenv("SCHOOL_STATE_PATH", state_file)
    monkeypatch.setenv("SCHOOL_DB_PATH", str(tmp_path / "school.db"))
    if app_module._sync_lock.locked():
        app_module._sync_lock.release()
    app_module._sync_lock.acquire()
    with patch("school_dashboard.digest.requests.post", side_effect=requests.Timeout("t")), \
         patch("school_dashboard.digest.build_afternoon_digest", return_value=("x", [])), \
         caplog.at_level(logging.ERROR):
        app_module._run_sync_background("none", digest_kind)
    assert app_module._sync_status["running"] is False
    assert app_module._sync_status["last_result"] == "error"
    assert "digest delivery failed" in caplog.text
    assert any(r.exc_info for r in caplog.records)
