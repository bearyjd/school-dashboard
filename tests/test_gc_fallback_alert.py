"""Rate-limited ntfy alert when GameChanger schedules come from calendar feeds."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from school_dashboard import gc_fallback_alert as fa

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def calls():
    return []


@pytest.fixture
def send(calls):
    return lambda topic, message, title: calls.append((topic, message, title))


def test_sent_once_then_suppressed_then_resent(tmp_path, send, calls, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "faketopic")
    p = tmp_path / "gc-fallback-alert.json"
    assert fa.maybe_alert(2, 3, now=NOW, send=send, path=p) is True
    assert calls == [("faketopic",
                      "Schedules are coming from calendar links only; game opponents may be missing. "
                      "The login needs attention.", "School: GameChanger login is failing")]
    assert fa.maybe_alert(2, 3, now=NOW + timedelta(hours=23), send=send, path=p) is False
    assert fa.maybe_alert(2, 3, now=NOW + timedelta(hours=25), send=send, path=p) is True
    assert len(calls) == 2


def test_not_sent_when_api_healthy(tmp_path, send, calls, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "faketopic")
    assert fa.maybe_alert(0, 3, now=NOW, send=send, path=tmp_path / "a.json") is False
    assert calls == []


def test_send_failure_does_not_raise_or_advance_state(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "faketopic")
    p = tmp_path / "a.json"

    def boom(*a):
        raise RuntimeError("503")

    assert fa.maybe_alert(1, 1, now=NOW, send=boom, path=p) is False
    assert not p.exists()


def test_no_topic_no_send(tmp_path, send, calls, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert fa.maybe_alert(1, 1, now=NOW, send=send, path=tmp_path / "a.json") is False
    assert calls == []


@pytest.mark.parametrize("stamp", ["2026-11-01T00:00:00", "2099-01-01T00:00:00+00:00", "garbage", 5])
def test_bad_last_sent_treated_as_absent(tmp_path, send, calls, monkeypatch, stamp):
    monkeypatch.setenv("NTFY_TOPIC", "faketopic")
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"last_sent": stamp}))
    assert fa.maybe_alert(1, 1, now=NOW, send=send, path=p) is True
    assert len(calls) == 1


def test_never_raises_even_if_state_dir_unwritable(tmp_path, send, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "faketopic")
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert fa.maybe_alert(1, 1, now=NOW, send=send, path=blocker / "sub" / "a.json") is False


def test_config_alert_own_title_state_and_limit(tmp_path, send, calls, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "faketopic")
    cfg, main = tmp_path / "cfg.json", tmp_path / "main.json"
    assert fa.maybe_config_alert(now=NOW, send=send, path=cfg) is True
    assert calls[0][1:] == ("The calendar feed file could not be read (check its permissions).",
                            "School: calendar feed config problem")
    assert fa.maybe_config_alert(now=NOW + timedelta(hours=1), send=send, path=cfg) is False
    assert fa.maybe_alert(1, 1, now=NOW + timedelta(hours=1), send=send, path=main) is True


def test_state_file_private_json(tmp_path, send, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "faketopic")
    p = tmp_path / "a.json"
    fa.maybe_alert(1, 1, now=NOW, send=send, path=p)
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert "last_sent" in json.loads(p.read_text())
