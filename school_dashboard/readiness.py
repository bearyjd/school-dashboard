# school_dashboard/readiness.py
"""Per-child readiness checklist: upcoming assignments, IXL remaining, tests."""
import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

_log = logging.getLogger(__name__)

_URGENCY_ORDER: dict[str, int] = {"overdue": 0, "tomorrow": 1, "soon": 2, "pending": 3}


def get_checklist(
    state_path: str,
    db_path: str | None = None,
    days_ahead: int = 3,
) -> dict[str, list[dict]]:
    """Return {child: [items]} for the readiness checklist.

    Each item: {"type": str, "label": str, "urgency": str, "detail": str}
    urgency values: "overdue" | "tomorrow" | "soon" | "pending"
    """
    try:
        state = json.loads(Path(state_path).read_text())
    except Exception as exc:
        _log.warning("Failed to load state %s: %s", state_path, exc)
        return {}

    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    # Fetch test/quiz events once for all children
    test_events: list[dict] = []
    if db_path and Path(db_path).exists():
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT date, title, type, child FROM events "
                    "WHERE type IN ('TEST','QUIZ') AND date >= ? AND date <= ? "
                    "ORDER BY date",
                    (today.isoformat(), cutoff.isoformat()),
                ).fetchall()
                test_events = [dict(r) for r in rows]
        except Exception as exc:
            _log.warning("Failed to query events from %s: %s", db_path, exc)

    result: dict[str, list[dict]] = {}

    for child, child_data in (state.get("schoology") or {}).items():
        items: list[dict] = []

        # Schoology assignments
        for a in child_data.get("assignments") or []:
            if a.get("status") in ("submitted", "graded", "completed"):
                continue
            due_str = (a.get("due_date") or "")[:10]
            if not due_str:
                continue
            try:
                due = date.fromisoformat(due_str)
            except ValueError:
                continue
            if due > cutoff:
                continue
            if due < today:
                urgency = "overdue"
            elif due == today + timedelta(days=1):
                urgency = "tomorrow"
            else:
                urgency = "soon"
            items.append({
                "type": "assignment",
                "label": a.get("title", ""),
                "urgency": urgency,
                "detail": a.get("course", ""),
            })

        # IXL remaining
        for subj, totals in ((state.get("ixl") or {}).get(child) or {}).get("totals", {}).items():
            remaining = totals.get("remaining", 0)
            if remaining > 0:
                label = f"{remaining} IXL skill{'s' if remaining != 1 else ''} remaining"
                items.append({
                    "type": "ixl",
                    "label": label,
                    "urgency": "pending",
                    "detail": subj,
                })

        # Tests / quizzes from DB
        for ev in test_events:
            if ev["child"] and ev["child"] != child:
                continue
            try:
                event_date = date.fromisoformat(ev["date"])
            except ValueError:
                continue
            urgency = "tomorrow" if event_date == today + timedelta(days=1) else "soon"
            items.append({
                "type": "test",
                "label": ev["title"],
                "urgency": urgency,
                "detail": ev["type"].title(),
            })

        items.sort(key=lambda x: _URGENCY_ORDER.get(x["urgency"], 4))

        if items:
            result[child] = items

    return result


def format_checklist_text(
    checklist: dict[str, list[dict]],
    prefix: str = "Action items:",
) -> str:
    """Return a plain-text checklist for digest injection. Empty string if nothing."""
    if not checklist:
        return ""
    lines: list[str] = []
    if prefix:
        lines.append(prefix)
    for child, items in checklist.items():
        lines.append(f"\n{child}:")
        for item in items:
            tag = f"[{item['urgency']}]"
            detail = f" ({item['detail']})" if item.get("detail") else ""
            lines.append(f"  - {item['label']}{detail} {tag}")
    return "\n".join(lines)
