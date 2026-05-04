# school_dashboard/llm.py
"""OpenAI-compatible chat client that survives streaming proxies.

Omniroute (and some LiteLLM routes) emit Server-Sent Events even when the
client did not request streaming. This helper sends ``stream: false`` for
correctness, then falls back to parsing SSE if the proxy ignores it.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

import requests

_log = logging.getLogger(__name__)


def _looks_like_sse(content_type: str, body: str) -> bool:
    if "text/event-stream" in content_type.lower():
        return True
    return body.lstrip().startswith("data:")


def _parse_sse(body: str) -> str:
    """Concatenate ``delta.content`` (or ``message.content``) from an SSE body.

    Tolerant of comment lines, blank lines, and the trailing ``[DONE]`` marker.
    """
    parts: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]" or not payload:
            continue
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in evt.get("choices") or []:
            delta = choice.get("delta") or {}
            chunk = delta.get("content")
            if chunk:
                parts.append(chunk)
                continue
            msg = choice.get("message") or {}
            if msg.get("content"):
                parts.append(msg["content"])
    return "".join(parts)


def _extract_non_streaming(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected response shape: {data!r}") from exc


def chat_completion(
    messages: list[dict] | str,
    url: str,
    api_key: str,
    model: str,
    *,
    max_tokens: int = 600,
    timeout: int = 60,
) -> str:
    """Send a chat completion and return the assistant reply text.

    ``messages`` may be either an OpenAI-style message list or a bare prompt
    string (which is wrapped as a single user turn).
    """
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]

    endpoint = f"{url.rstrip('/')}/v1/chat/completions"
    resp = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        },
        timeout=timeout,
    )
    if not resp.ok:
        raise RuntimeError(
            f"chat_completion HTTP {resp.status_code} from {endpoint}: "
            f"{resp.text[:300]!r}"
        )

    body = resp.text
    content_type = resp.headers.get("content-type", "")

    if _looks_like_sse(content_type, body):
        text = _parse_sse(body)
        if not text:
            raise ValueError(
                f"SSE response had no content. content-type={content_type!r} "
                f"body[:300]={body[:300]!r}"
            )
        return text

    try:
        return _extract_non_streaming(resp.json())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Non-JSON, non-SSE response. content-type={content_type!r} "
            f"body[:300]={body[:300]!r}"
        ) from exc
