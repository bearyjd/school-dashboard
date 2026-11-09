"""Tests for school_dashboard.llm.chat_completion (SSE-tolerant LLM client)."""
import json
from unittest.mock import patch, MagicMock

import pytest

from school_dashboard.llm import (
    chat_completion,
    _looks_like_sse,
    _parse_sse,
)


def _resp(body: str, content_type: str, ok: bool = True, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.text = body
    r.headers = {"content-type": content_type}
    r.ok = ok
    r.status_code = status
    r.json.side_effect = lambda: json.loads(body)
    return r


SSE_BODY = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"Hello "}}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"world"}}]}\n\n'
    "data: [DONE]\n\n"
    ": x-omniroute-cache-hit=false\n"
)


def test_parse_sse_concatenates_delta_content():
    assert _parse_sse(SSE_BODY) == "Hello world"


def test_parse_sse_handles_message_content_envelope():
    body = 'data: {"choices":[{"message":{"content":"single"}}]}\n\n'
    assert _parse_sse(body) == "single"


def test_parse_sse_skips_done_and_comments():
    body = (
        ": comment line\n"
        'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        "data: [DONE]\n"
    )
    assert _parse_sse(body) == "a"


def test_looks_like_sse_by_content_type():
    assert _looks_like_sse("text/event-stream", "irrelevant") is True


def test_looks_like_sse_by_body_sniff():
    assert _looks_like_sse("application/json", "data: hi") is True


def test_looks_like_sse_negative():
    assert _looks_like_sse("application/json", '{"ok": 1}') is False


@patch("school_dashboard.llm.requests.post")
def test_chat_completion_handles_sse_response(mock_post):
    mock_post.return_value = _resp(SSE_BODY, "text/event-stream")
    out = chat_completion("hi", "http://fake", "k", "m")
    assert out == "Hello world"


@patch("school_dashboard.llm.requests.post")
def test_chat_completion_handles_classic_json(mock_post):
    body = json.dumps({"choices": [{"message": {"content": "json hi"}}]})
    mock_post.return_value = _resp(body, "application/json")
    assert chat_completion("hi", "http://fake", "k", "m") == "json hi"


@patch("school_dashboard.llm.requests.post")
def test_chat_completion_accepts_message_list(mock_post):
    body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
    mock_post.return_value = _resp(body, "application/json")
    msgs = [{"role": "user", "content": "hi"}]
    chat_completion(msgs, "http://fake", "k", "m")
    sent = mock_post.call_args.kwargs["json"]
    assert sent["messages"] == msgs
    assert sent["stream"] is False


@patch("school_dashboard.llm.requests.post")
def test_chat_completion_raises_with_diagnostics_on_garbage(mock_post):
    mock_post.return_value = _resp("<html>500 boom</html>", "text/html")
    with pytest.raises(ValueError) as exc_info:
        chat_completion("hi", "http://fake", "k", "m")
    msg = str(exc_info.value)
    assert "text/html" in msg
    assert "500 boom" in msg


@patch("school_dashboard.llm.requests.post")
def test_chat_completion_raises_on_http_error(mock_post):
    mock_post.return_value = _resp("nope", "text/plain", ok=False, status=502)
    with pytest.raises(RuntimeError) as exc_info:
        chat_completion("hi", "http://fake", "k", "m")
    assert "502" in str(exc_info.value)


@patch("school_dashboard.llm.requests.post")
def test_chat_completion_raises_on_empty_sse(mock_post):
    mock_post.return_value = _resp(
        "data: [DONE]\n\n", "text/event-stream"
    )
    with pytest.raises(ValueError, match="no content"):
        chat_completion("hi", "http://fake", "k", "m")


# --- retry on transient proxy failures (reasoning model exhausting max_tokens -> 502) ---

_OK_BODY = json.dumps({"choices": [{"message": {"content": "hi"}}]})
_BAD_502 = ('{"error":{"message":"reasoning consumed 576/576 tokens - no content output"}}',
            "application/json")


def _bad(status: int = 502) -> MagicMock:
    return _resp(_BAD_502[0], _BAD_502[1], ok=False, status=status)


def test_chat_completion_retries_502_then_succeeds():
    with patch("school_dashboard.llm.time.sleep") as sleep, patch(
        "school_dashboard.llm.requests.post",
        side_effect=[_bad(), _resp(_OK_BODY, "application/json")],
    ) as post:
        assert chat_completion("p", "http://x", "k", "m", max_attempts=3) == "hi"
    assert post.call_count == 2
    sleep.assert_called_once()


def test_chat_completion_raises_after_exhausting_retries():
    with patch("school_dashboard.llm.time.sleep"), patch(
        "school_dashboard.llm.requests.post", side_effect=[_bad(), _bad(), _bad()]
    ) as post:
        with pytest.raises(RuntimeError, match="HTTP 502"):
            chat_completion("p", "http://x", "k", "m", max_attempts=3)
    assert post.call_count == 3


def test_chat_completion_does_not_retry_4xx():
    with patch("school_dashboard.llm.time.sleep") as sleep, patch(
        "school_dashboard.llm.requests.post", side_effect=[_bad(401)]
    ) as post:
        with pytest.raises(RuntimeError, match="HTTP 401"):
            chat_completion("p", "http://x", "k", "m", max_attempts=3)
    assert post.call_count == 1
    sleep.assert_not_called()


def test_digest_gives_reasoning_models_token_headroom():
    from school_dashboard.digest import _call_litellm

    with patch("school_dashboard.digest.chat_completion", return_value="ok") as cc:
        _call_litellm("p", "http://x", "k", "m")
    assert cc.call_args.kwargs["max_tokens"] >= 2000


def test_chat_completion_sends_reasoning_effort_only_when_set():
    with patch("school_dashboard.llm.requests.post",
               return_value=_resp(_OK_BODY, "application/json")) as post:
        chat_completion("p", "http://x", "k", "m")
        assert "reasoning_effort" not in post.call_args.kwargs["json"]
        chat_completion("p", "http://x", "k", "m", reasoning_effort="low")
        assert post.call_args.kwargs["json"]["reasoning_effort"] == "low"


def test_digest_requests_low_reasoning_effort():
    from school_dashboard.digest import _call_litellm

    with patch("school_dashboard.digest.chat_completion", return_value="ok") as cc:
        _call_litellm("p", "http://x", "k", "m")
    assert cc.call_args.kwargs["reasoning_effort"] == "low"


def test_chat_completion_does_not_retry_by_default():
    """Interactive callers (/api/chat) must fail fast, not block on retries."""
    with patch("school_dashboard.llm.time.sleep") as sleep, patch(
        "school_dashboard.llm.requests.post", side_effect=[_bad()]
    ) as post:
        with pytest.raises(RuntimeError, match="HTTP 502"):
            chat_completion("p", "http://x", "k", "m")
    assert post.call_count == 1
    sleep.assert_not_called()


def test_digest_opts_in_to_retries():
    from school_dashboard.digest import _call_litellm

    with patch("school_dashboard.digest.chat_completion", return_value="ok") as cc:
        _call_litellm("p", "http://x", "k", "m")
    assert cc.call_args.kwargs["max_attempts"] == 3


def test_digest_reasoning_effort_env_override(monkeypatch):
    from school_dashboard.digest import _call_litellm

    monkeypatch.setenv("LITELLM_REASONING_EFFORT", "minimal")
    with patch("school_dashboard.digest.chat_completion", return_value="ok") as cc:
        _call_litellm("p", "http://x", "k", "m")
    assert cc.call_args.kwargs["reasoning_effort"] == "minimal"
    monkeypatch.setenv("LITELLM_REASONING_EFFORT", "")
    with patch("school_dashboard.digest.chat_completion", return_value="ok") as cc:
        _call_litellm("p", "http://x", "k", "m")
    assert cc.call_args.kwargs["reasoning_effort"] is None
