"""cmd_digest with RECON_DIGEST_ENABLED (S7) and `assignments digest` preview."""
import argparse
import builtins
import hashlib
import json
import logging
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from recon import digest_support as ds
from school_dashboard import cli

FLAG = "RECON_DIGEST_ENABLED"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in (FLAG, "RECON_DIGEST_LLM", "RECON_PUSH_ENABLED", "LITELLM_URL", "GOG_ACCOUNT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NTFY_TOPIC", "synthetic-topic")


@pytest.fixture
def api(tmp_path, monkeypatch):
    return ds.friday_env(tmp_path, monkeypatch)


def _ok():
    resp = MagicMock()
    resp.ok = True
    resp.raise_for_status.return_value = None
    return resp


def _run(mode, post=None):
    post = post or MagicMock(return_value=_ok())
    with patch("school_dashboard.digest.requests.post", post):
        cli.cmd_digest(argparse.Namespace(mode=mode, state_file=None))
    return post


def _fixed_now(monkeypatch, now):
    from school_dashboard.assignments import digest_run
    real = digest_run.build_for_cron
    monkeypatch.setattr(digest_run, "build_for_cron", lambda mode, *, now=None: real(mode, now=now_value))
    now_value = now


# ---- flag off: byte-for-byte v1 -------------------------------------------

@pytest.mark.parametrize("mode,builder", [("morning", "build_morning_digest"), ("night", "build_night_digest")])
def test_flag_off_uses_the_v1_builder_and_never_touches_recon(api, monkeypatch, mode, builder):
    monkeypatch.setenv("LITELLM_URL", "http://x/v1")
    with patch(f"school_dashboard.digest.{builder}", return_value=("v1 text", [])) as v1, \
         patch("school_dashboard.assignments.digest_run.build_for_cron") as v2:
        post = _run(mode)
    v1.assert_called_once()
    v2.assert_not_called()
    assert post.call_args.kwargs["data"] == b"v1 text"


def test_flag_off_still_requires_litellm_url(api, capsys):
    with pytest.raises(SystemExit) as exc:
        _run("morning")
    assert exc.value.code == 1 and "LITELLM_URL not set" in capsys.readouterr().err


def test_flag_off_fallback_keeps_the_ai_note(api, monkeypatch):
    monkeypatch.setenv("LITELLM_URL", "http://x/v1")
    with patch("school_dashboard.digest.build_night_digest", side_effect=RuntimeError("llm")):
        post = MagicMock(return_value=_ok())
        with pytest.raises(SystemExit) as exc:
            _run("night", post)
    assert exc.value.code == 1
    assert post.call_args.kwargs["data"].decode().startswith("AI summary unavailable - showing the basic")


# ---- flag on ---------------------------------------------------------------

@pytest.mark.parametrize("mode,title", [("morning", "Morning Briefing"), ("night", "Night Prep")])
def test_flag_on_sends_the_deterministic_text_without_litellm(api, monkeypatch, mode, title, dashboard_url):
    monkeypatch.setenv(FLAG, "1")
    _fixed_now(monkeypatch, ds.SAT)
    with patch("school_dashboard.digest.build_morning_digest") as m, patch("school_dashboard.digest.build_night_digest") as n:
        post = _run(mode)
    m.assert_not_called()
    n.assert_not_called()
    kw = post.call_args.kwargs
    assert kw["headers"]["Title"] == title
    assert kw["data"].decode() == ds.golden("saturday_missing").rstrip("\n").replace(
        "Do tonight", "Due next" if mode == "morning" else "Do tonight")
    assert "digest=" in kw["headers"]["Click"]
    rows = api.q("SELECT title, cards FROM digests")
    assert [r["title"] for r in rows] == [title]
    assert json.loads(rows[0]["cards"])[0]["child"] == "Alex"


def test_flag_on_zero_card_empty_db_digest_is_still_delivered(tmp_path, monkeypatch):
    api = ds.friday_env(tmp_path, monkeypatch, seed=[])
    monkeypatch.setenv(FLAG, "yes")
    _fixed_now(monkeypatch, ds.SAT)
    post = _run("night")
    assert post.call_count == 1 and "Alex: nothing on record" in post.call_args.kwargs["data"].decode()
    rows = api.q("SELECT title, cards FROM digests")
    assert [(r["title"], r["cards"]) for r in rows] == [("Night Prep", "[]")]


def test_flag_on_does_not_call_gog(api, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("GOG_ACCOUNT", "a@example.invalid")
    _fixed_now(monkeypatch, ds.SAT)
    with patch("school_dashboard.cli.fetch_gcal_events") as gcal:
        _run("night")
    gcal.assert_not_called()


def test_flag_on_still_requires_ntfy_topic(api, monkeypatch, capsys):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.delenv("NTFY_TOPIC")
    with pytest.raises(SystemExit) as exc:
        _run("night")
    assert exc.value.code == 1 and "NTFY_TOPIC not set" in capsys.readouterr().err


def test_afternoon_is_unaffected_by_the_flag(api, monkeypatch, capsys):
    monkeypatch.setenv(FLAG, "1")
    with pytest.raises(SystemExit) as exc:  # no LITELLM_URL: afternoon still needs it
        _run("afternoon")
    assert exc.value.code == 1 and "LITELLM_URL not set" in capsys.readouterr().err
    monkeypatch.setenv("LITELLM_URL", "http://x/v1")
    with patch("school_dashboard.digest.build_afternoon_digest", return_value=("v1 pm", [])) as v1, \
         patch("school_dashboard.assignments.digest_run.build_for_cron") as v2:
        post = _run("afternoon")
    v1.assert_called_once()
    v2.assert_not_called()
    assert post.call_args.kwargs["headers"]["Title"] == "Homework Check"


def test_v2_failure_sends_quick_check_with_its_own_note_and_exits_1(tmp_path, monkeypatch, capsys):
    ds.friday_env(tmp_path, monkeypatch, migrated=False)
    monkeypatch.setenv(FLAG, "1")
    post = MagicMock(return_value=_ok())
    with pytest.raises(SystemExit) as exc:
        _run("morning", post)
    assert exc.value.code == 1
    kw = post.call_args.kwargs
    assert kw["data"].decode().startswith("Assignment list unavailable - showing the basic homework check instead.")
    assert "AI summary" not in kw["data"].decode()
    assert kw["headers"]["Title"] == "Morning Briefing (fallback)"
    assert "digest build failed" in capsys.readouterr().err


def test_deliver_digest_default_note_is_unchanged():
    from school_dashboard import digest
    assert digest.FALLBACK_NOTE == "AI summary unavailable - showing the basic homework check instead.\n\n"


# ---- run order and recon guard ---------------------------------------------------

def _order_env(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    _fixed_now(monkeypatch, ds.SAT)
    order = []
    monkeypatch.setattr("school_dashboard.assignments.digest_run.flush_outbox", lambda *a, **k: order.append("flush"))
    post = MagicMock(return_value=_ok(), side_effect=lambda *a, **k: order.append("post") or _ok())
    return order, post


def test_outbox_is_flushed_after_the_digest_push(api, monkeypatch):
    order, post = _order_env(monkeypatch)
    _run("night", post)
    assert order == ["post", "flush"]


def test_outbox_is_still_flushed_after_the_fallback_push(tmp_path, monkeypatch):
    ds.friday_env(tmp_path, monkeypatch, migrated=False)
    order, post = _order_env(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run("morning", post)
    assert exc.value.code == 1 and order == ["post", "flush"]


def test_flag_off_never_flushes(api, monkeypatch):
    monkeypatch.setenv("LITELLM_URL", "http://x/v1")
    flush = MagicMock()
    monkeypatch.setattr("school_dashboard.assignments.digest_run.flush_outbox", flush)
    with patch("school_dashboard.digest.build_night_digest", return_value=("v1 text", [])):
        _run("night")
    flush.assert_not_called()


def _v1_still_used(monkeypatch, caplog, kind):
    monkeypatch.setenv("LITELLM_URL", "http://x/v1")
    with patch("school_dashboard.digest.build_morning_digest", return_value=("v1 text", [])) as v1, \
         patch("school_dashboard.assignments.digest_run.build_for_cron") as v2, caplog.at_level(logging.ERROR):
        post = _run("morning")
    v1.assert_called_once()
    v2.assert_not_called()
    assert post.call_args.kwargs["data"] == b"v1 text"
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and kind in errors[0] and "secret-detail" not in errors[0]


def test_broken_flag_evaluation_falls_back_to_v1_with_one_error_log(api, monkeypatch, caplog):
    monkeypatch.setenv(FLAG, "1")

    def boom(name):
        raise RuntimeError("secret-detail")
    monkeypatch.setattr("school_dashboard.assignments.flagsenv.enabled", boom)
    _v1_still_used(monkeypatch, caplog, "RuntimeError")


def test_failing_flagsenv_import_falls_back_to_v1_with_one_error_log(api, monkeypatch, caplog):
    monkeypatch.setenv(FLAG, "1")
    real = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "school_dashboard.assignments" and "flagsenv" in (fromlist or ()):
            raise ImportError("secret-detail")
        return real(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, "__import__", fake)
    _v1_still_used(monkeypatch, caplog, "ImportError")


# ---- `assignments digest` preview ----------------------------------------------

def _preview(capsys, *extra, mode="night", now=ds.SAT.isoformat()):
    from school_dashboard.assignments import cli as acli
    ns = argparse.Namespace(assignments_command="digest", mode=mode, now=now, json="--json" in extra)
    with pytest.raises(SystemExit) as exc:
        acli.cmd_assignments(ns)
    out = capsys.readouterr()
    return exc.value.code, out.out, out.err


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_preview_prints_the_golden_text_and_sends_nothing(api, monkeypatch, capsys):
    monkeypatch.setenv("LITELLM_URL", "http://x/v1")
    monkeypatch.setenv("RECON_DIGEST_LLM", "1")
    before = _sha(api.db)
    with patch("school_dashboard.digest.requests.post", side_effect=AssertionError("sent")), \
         patch("requests.post", side_effect=AssertionError("sent")), \
         patch("school_dashboard.llm.chat_completion", side_effect=AssertionError("llm")), \
         patch("school_dashboard.assignments.ingest.recompute", side_effect=AssertionError("recompute")), \
         patch("school_dashboard.assignments.notify.send_pending", side_effect=AssertionError("flush")):
        code, out, _ = _preview(capsys)
    assert code == 0 and out == ds.golden("saturday_missing")
    assert _sha(api.db) == before  # nothing written, not even a flag recompute


def test_preview_json_has_text_and_cards(api, capsys):
    code, out, _ = _preview(capsys, "--json")
    data = json.loads(out)
    assert code == 0 and data["text"] + "\n" == ds.golden("saturday_missing")
    assert data["cards"][0]["detail"] == "Missing: Math 6, due Nov 20"


def test_preview_morning_uses_the_morning_heading(api, capsys):
    _, out, _ = _preview(capsys, mode="morning", now=ds.FRI.isoformat())
    assert "Due next (1)" in out


def test_preview_bad_now_exits_2(api, capsys):
    code, out, err = _preview(capsys, now="not-a-time")
    assert code == 2 and out == "" and "ISO 8601" in err


def test_preview_not_migrated_exits_3(tmp_path, monkeypatch, capsys):
    ds.friday_env(tmp_path, monkeypatch, migrated=False)
    code, _, err = _preview(capsys)
    assert code == 3 and err.startswith("Error:")


def test_preview_parser_wiring(api):
    p = argparse.ArgumentParser()
    from school_dashboard.assignments import cli as acli
    acli.add_parser(p.add_subparsers())
    args = p.parse_args(["assignments", "digest", "--mode", "night", "--now", "2026-11-21T10:00:00-05:00", "--json"])
    assert (args.mode, args.now, args.json) == ("night", "2026-11-21T10:00:00-05:00", True)
    with pytest.raises(SystemExit):
        p.parse_args(["assignments", "digest", "--mode", "afternoon"])


def test_preview_naive_now_gets_the_configured_zone(api, capsys):
    code, out, _ = _preview(capsys, now="2026-11-21T10:00:00")
    assert code == 0 and "Missing: Report" in out
