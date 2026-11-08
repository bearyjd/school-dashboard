"""Capture the exact LLM messages each digest builder sends, from a synthetic state."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from school_dashboard import digest

HEALTHY_STATE = {
    "schoology": {"Alpha": {"updated": "2026-04-09", "assignments": [
        {"title": "Math HW", "due_date": "2026-04-10", "course": "Math", "status": ""},
        {"title": "Essay", "due_date": "2026-04-11", "course": "ELA", "status": ""}]}},
    "ixl": {"Alpha": {"updated": "2026-04-09",
                      "totals": {"Math": {"remaining": 3, "assigned": 5, "done": 2}}}},
    "action_items": [{"child": "Alpha", "source": "email", "summary": "Return form", "due": "2026-04-10"}],
}


def _resp() -> MagicMock:
    body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
    r = MagicMock(ok=True, status_code=200, text=body, headers={"content-type": "application/json"})
    r.json = lambda: json.loads(body)
    return r


def capture_all(tmp: Path, state: dict, now=None) -> dict:
    """{builder name: messages sent to requests.post} for the four LLM builders (weekly: both modes)."""
    sp, fp, dp = tmp / "s.json", tmp / "f.json", tmp / "s.db"
    sp.write_text(json.dumps(state))
    fp.write_text(json.dumps([{"subject": "Alpha", "fact": "Soccer on Tuesdays"}]))
    conn = sqlite3.connect(str(dp))
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, date TEXT, title TEXT, type TEXT, child TEXT)")
    conn.commit()
    conn.close()
    llm = ("http://x", "k", "m")
    calls = {
        "morning": lambda: digest.build_morning_digest(str(sp), str(dp), str(fp), [], *llm, today="2026-04-10", now=now),
        "afternoon": lambda: digest.build_afternoon_digest(str(sp), *llm, today="2026-04-10", now=now),
        "night": lambda: digest.build_night_digest(str(sp), str(dp), str(fp), [], *llm, tomorrow="2026-04-11", now=now),
        "friday": lambda: digest.build_weekly_digest("friday", str(sp), str(dp), str(fp), *llm, today="2026-04-10", now=now),
        "sunday": lambda: digest.build_weekly_digest("sunday", str(sp), str(dp), str(fp), *llm, today="2026-04-12", now=now),
    }
    out = {}
    for name, fn in calls.items():
        with patch("school_dashboard.llm.requests.post", return_value=_resp()) as post:
            fn()
        out[name] = post.call_args.kwargs["json"]["messages"]
    return out
