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


def test_state_file_private_json(tmp_path, send, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "faketopic")
    p = tmp_path / "a.json"
    fa.maybe_alert(1, 1, now=NOW, send=send, path=p)
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert "last_sent" in json.loads(p.read_text())
