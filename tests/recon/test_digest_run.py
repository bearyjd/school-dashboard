"""I/O shell of the v2 digest (S7): recompute-before-build, AI paragraph, outbox flush, failure isolation."""
import logging
from unittest.mock import MagicMock

import pytest
import requests

from recon import digest_support as ds

LLM_URL = "https://llm.example.invalid/v1"
HEAD = "AI summary (may be wrong; the list above is the source):"


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    for name in ("RECON_DIGEST_LLM", "RECON_PUSH_ENABLED", "LITELLM_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def api(tmp_path, monkeypatch):
    return ds.friday_env(tmp_path, monkeypatch)


def _build(now=ds.SAT, mode="night"):
    from school_dashboard.assignments import digest_run
    return digest_run.build_for_cron(mode, now=now)


def _llm_on(monkeypatch, reply):
    monkeypatch.setenv("RECON_DIGEST_LLM", "1")
    monkeypatch.setenv("LITELLM_URL", LLM_URL)
    mock = MagicMock(side_effect=reply) if isinstance(reply, BaseException) else MagicMock(return_value=reply)
    monkeypatch.setattr("school_dashboard.llm.chat_completion", mock)
    return mock


def test_returns_text_and_card_dicts(api):
    text, cards = _build()
    assert text + "\n" == ds.golden("saturday_missing")
    assert cards == [{"source": "recon", "child": "Alex", "title": "Report", "detail": "Missing: Math 6, due Nov 20",
                      "due_date": "2026-11-20", "url": "", "done": False}]
    assert isinstance(cards, list) and isinstance(cards[0], dict)


def test_recompute_runs_before_build_so_stored_flags_follow_the_clock(api):
    assert api.q("SELECT flag FROM assignments")[0]["flag"] != "missing"
    _build(ds.SAT)
    assert api.q("SELECT flag FROM assignments")[0]["flag"] == "missing"
    assert api.q("SELECT COUNT(*) AS n FROM assignment_events WHERE kind = 'flag'")[0]["n"] >= 1


def test_busy_kid_is_skipped_with_a_counted_note(api, monkeypatch, caplog):
    from school_dashboard.assignments import ingest
    from school_dashboard.assignments.ingest_types import KidResult
    real = ingest.recompute

    def flaky(conn, kid, **kw):
        return KidResult(kid=kid, ok=False, error="BusyError") if kid == "Sam" else real(conn, kid, **kw)
    monkeypatch.setattr(ingest, "recompute", flaky)
    with caplog.at_level(logging.WARNING):
        text, _ = _build()
    assert text.endswith("Notes: 1 kid(s) could not be refreshed; showing stored data.")
    assert "Missing: Report" in text  # still built from what is stored (flags computed on read)
    assert "Sam" not in caplog.text


def test_recompute_raising_is_also_skipped(api, monkeypatch):
    from school_dashboard.assignments import ingest

    def boom(*a, **kw):
        raise RuntimeError("disk")
    monkeypatch.setattr(ingest, "recompute", boom)
    text, _ = _build()
    assert text.endswith("Notes: 3 kid(s) could not be refreshed; showing stored data.")


def test_not_migrated_raises(tmp_path, monkeypatch):
    from school_dashboard.assignments import repo
    ds.friday_env(tmp_path, monkeypatch, migrated=False)
    with pytest.raises(repo.NotMigratedError):
        _build()


def test_missing_database_raises(tmp_path, monkeypatch):
    from school_dashboard.assignments import repo
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(tmp_path / "empty"))
    with pytest.raises(repo.NotMigratedError):
        _build()


def test_no_llm_call_when_flag_off_even_with_url_set(api, monkeypatch):
    monkeypatch.setenv("LITELLM_URL", LLM_URL)
    post = MagicMock(side_effect=AssertionError("network call"))
    monkeypatch.setattr(requests, "post", post)
    _build()
    post.assert_not_called()


def test_no_llm_call_when_flag_on_but_no_url(api, monkeypatch):
    mock = _llm_on(monkeypatch, "x")
    monkeypatch.delenv("LITELLM_URL")
    text, _ = _build()
    mock.assert_not_called()
    assert HEAD not in text


def test_ai_paragraph_is_appended_after_the_list(api, monkeypatch):
    plain, _ = _build()
    mock = _llm_on(monkeypatch, "Alex has one missing report.")
    text, _ = _build()
    assert text == f"{plain}\n\n{HEAD}\nAlex has one missing report."
    assert mock.call_count == 1
    args, kw = mock.call_args
    from school_dashboard.assignments import digest_run
    messages = args[0]
    assert messages[0] == {"role": "system", "content": digest_run.RECON_SUMMARY_PROMPT}
    assert plain in messages[1]["content"] and "Notes:" not in messages[1]["content"]
    assert kw["max_attempts"] == 1 and kw["timeout"] <= 30


def test_ai_paragraph_goes_before_the_notes_line(api, monkeypatch):
    from school_dashboard.assignments import ingest
    from school_dashboard.assignments.ingest_types import KidResult
    monkeypatch.setattr(ingest, "recompute", lambda conn, kid, **kw: KidResult(kid=kid, ok=False))
    _llm_on(monkeypatch, "Short.")
    text, _ = _build()
    assert text.index(HEAD) < text.index("Notes:") and text.endswith("showing stored data.")


@pytest.mark.parametrize("reply", [
    RuntimeError("boom"), requests.Timeout("slow"), requests.ConnectionError("down"), "", "   \x00\x07 ", "y" * 5000,
    "z" * 401])
def test_failed_or_unusable_ai_leaves_the_text_byte_identical(api, monkeypatch, reply, caplog):
    plain, plain_cards = _build()
    _llm_on(monkeypatch, reply)
    with caplog.at_level(logging.WARNING):
        text, cards = _build()
    assert text == plain and cards == plain_cards
    assert sum("AI summary omitted" in r.getMessage() for r in caplog.records) == 1


def test_warning_names_the_exception_type_only(api, monkeypatch, caplog):
    _llm_on(monkeypatch, RuntimeError("secret-detail-123"))
    with caplog.at_level(logging.WARNING):
        _build()
    assert "RuntimeError" in caplog.text and "secret-detail-123" not in caplog.text


def test_control_characters_are_stripped_from_the_ai_paragraph(api, monkeypatch):
    _llm_on(monkeypatch, "Fine\x00 summary\x1b[31m here.\nSecond line.")
    text, _ = _build()
    tail = text.split(HEAD + "\n")[1]
    assert tail == "Fine summary[31m here. Second line." and "\x1b" not in text and "\x00" not in text


def test_exactly_400_chars_is_accepted(api, monkeypatch):
    _llm_on(monkeypatch, "w" * 400)
    assert ("w" * 400) in _build()[0]


def test_outbox_flush_is_called_best_effort_and_failures_are_ignored(api, monkeypatch):
    plain, _ = _build()
    calls = []

    def boom(conn, *, now, send=None):
        calls.append(now)
        raise RuntimeError("ntfy down")
    monkeypatch.setattr("school_dashboard.assignments.notify.send_pending", boom)
    text, _ = _build()
    assert calls == [ds.SAT] and text == plain


def test_outbox_flush_really_sends_pending_rows_when_enabled(api, monkeypatch):
    sent = []
    monkeypatch.setenv("RECON_PUSH_ENABLED", "1")
    monkeypatch.setenv("NTFY_TOPIC", "synthetic-topic")
    monkeypatch.setattr("school_dashboard.assignments.notify.default_send", lambda t, m, ti: sent.append(m))
    from school_dashboard.assignments import repo, schema
    conn = schema.connect(api.db)
    with repo.transaction(conn):
        aid = conn.execute("SELECT id FROM assignments").fetchone()["id"]
        repo.enqueue_push(conn, aid, "missing", ds.SAT, "transition")
    conn.close()
    _build()
    assert len(sent) == 1 and "Report" in sent[0]


def test_connection_is_closed_after_build(api, monkeypatch):
    opened = []
    from school_dashboard.assignments import readctx
    real = readctx.open_db

    def spy(**kw):
        c = real(**kw)
        opened.append(c)
        return c
    monkeypatch.setattr(readctx, "open_db", spy)
    _build()
    with pytest.raises(Exception):
        opened[0].execute("SELECT 1")


def test_default_now_uses_the_configured_zone(api):
    from school_dashboard.assignments import digest_run
    text, _ = digest_run.build_for_cron("morning")
    assert text.startswith("Data as of")
