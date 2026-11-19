"""Push outbox sender: off by default, silent until the baseline run, one push per kid per call.

Logs carry counts and exception type names only; the ntfy topic and any title never reach a log.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Mapping, Sequence

import requests

from school_dashboard import maintenance

from . import flagsenv, repo

logger = logging.getLogger(__name__)

PUSH_FLAG = "RECON_PUSH_ENABLED"
MAX_ATTEMPTS = 5
MAX_PENDING_AGE = timedelta(hours=24)
PUSH_TITLE = "School: needs action"
_LABEL = {"disputed": "Disputed", "missing": "Missing"}
_TIMEOUT_SECONDS = 15

Send = Callable[[str, str, str], None]  # (topic, message, title)


@dataclass(frozen=True)
class SendReport:
    sent: int = 0
    failed: int = 0
    suppressed: int = 0
    exhausted: int = 0
    skipped: str | None = None


def default_send(topic: str, message: str, title: str) -> None:
    """POST to ntfy with priority high; raises on any HTTP error or timeout."""
    from school_dashboard.digest import DASHBOARD_BASE
    resp = requests.post(
        f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "school", "Click": DASHBOARD_BASE},
        timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()


def _candidates(conn: sqlite3.Connection) -> list[Mapping[str, object]]:
    rows = conn.execute(
        "SELECT o.id, o.flag, o.attempts, o.created_at, a.kid, a.title, a.class_name FROM push_outbox o"
        " JOIN assignments a ON a.id = o.assignment_id WHERE o.status IN ('pending', 'failed') ORDER BY o.id")
    return [dict(r) for r in rows.fetchall()]


def _expire(conn: sqlite3.Connection, rows: Sequence[Mapping[str, object]], now: datetime) -> tuple[list[Mapping[str, object]], int]:
    """Mark rows older than MAX_PENDING_AGE suppressed; returns (still live rows, expired count)."""
    live: list[Mapping[str, object]] = []
    expired = 0
    for row in rows:
        if now - datetime.fromisoformat(str(row["created_at"])) > MAX_PENDING_AGE:
            conn.execute("UPDATE push_outbox SET status = 'suppressed', last_error = 'expired' WHERE id = ?",
                         (row["id"],))
            expired += 1
        else:
            live.append(row)
    return live, expired


def _line(row: Mapping[str, object]) -> str:
    where = f" ({row['class_name']})" if row["class_name"] else ""
    return f"{row['kid']}: {row['title']} — {_LABEL.get(str(row['flag']), str(row['flag']))}{where}"


def _push_kid(conn: sqlite3.Connection, rows: Sequence[Mapping[str, object]], topic: str, send: Send,
              now: datetime) -> bool:
    error: str | None = None
    try:
        send(topic, "\n".join(_line(r) for r in rows), PUSH_TITLE)
    except Exception as exc:
        error = type(exc).__name__
    with repo.transaction(conn):
        for row in rows:
            if error is None:
                repo.mark_push(conn, int(str(row["id"])), "sent", now=now)
            else:
                repo.mark_push(conn, int(str(row["id"])), "failed", now=now, error=error)
    return error is None


def send_pending(conn: sqlite3.Connection, *, now: datetime, send: Send | None = None) -> SendReport:
    if not flagsenv.enabled(PUSH_FLAG):
        return SendReport(skipped="push disabled")
    if maintenance.is_active():
        return SendReport(skipped="maintenance")
    if repo.get_meta(conn, "baseline_at") is None:
        return SendReport(skipped="no baseline")
    with repo.transaction(conn):
        live, expired = _expire(conn, _candidates(conn), now)
    exhausted = [r for r in live if int(str(r["attempts"])) >= MAX_ATTEMPTS]
    due = [r for r in live if r not in exhausted]
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        if due:
            logger.error("recon push: NTFY_TOPIC is not set, %d pushes stay pending", len(due))
        return SendReport(suppressed=expired, exhausted=len(exhausted), skipped="no topic")
    by_kid: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in due:
        by_kid[str(row["kid"])].append(row)
    sent = failed = 0
    for kid in sorted(by_kid):
        try:
            ok = _push_kid(conn, by_kid[kid], topic, send or default_send, now)
        except Exception as exc:  # marks could not be written (e.g. busy); the next call retries
            logger.error("recon push: could not record results for one kid (%s)", type(exc).__name__)
            continue
        sent, failed = (sent + len(by_kid[kid]), failed) if ok else (sent, failed + len(by_kid[kid]))
    logger.info("recon push: %d sent, %d failed, %d expired, %d exhausted", sent, failed, expired, len(exhausted))
    return SendReport(sent=sent, failed=failed, suppressed=expired, exhausted=len(exhausted))
