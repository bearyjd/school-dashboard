import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path
import requests
from flask import Flask, jsonify, render_template, request, Response
from school_dashboard.gcal import fetch_gcal_events

app = Flask(__name__)

LITELLM_URL = os.environ.get("LITELLM_URL", "http://192.168.1.20:4000/")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "claude-sonnet")
STATE_PATH = os.environ.get("SCHOOL_STATE_PATH", "/opt/school/state/school-state.json")
EMAIL_DIGEST_PATH = os.environ.get("SCHOOL_EMAIL_DIGEST", "/opt/school/state/email-digest.json")
DB_PATH = os.environ.get("SCHOOL_DB_PATH", "/opt/school/state/school.db")
FACTS_PATH = os.environ.get("SCHOOL_FACTS_PATH", "/opt/school/state/facts.json")
DASHBOARD_HTML = os.environ.get("SCHOOL_DASHBOARD_HTML", "/opt/school/state/school-dashboard.html")
GOG_ACCOUNT = os.environ.get("GOG_ACCOUNT", "")
SGY_BASE_URL = os.environ.get("SGY_BASE_URL", "https://arlingtondiocese.schoology.com")


def load_json(path: str) -> dict | list:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"could not load {path}: {e}"}


def load_upcoming_events(days: int = 30) -> list[dict]:
    if not Path(DB_PATH).exists():
        return []
    try:
        from_date = date.today().isoformat()
        end_date = (date.today() + timedelta(days=days)).isoformat()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT date, title, type, child FROM events WHERE date >= ? AND date < ? ORDER BY date",
            (from_date, end_date),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def load_facts() -> list[dict]:
    try:
        p = Path(FACTS_PATH)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return []


def build_system_prompt() -> str:
    state = load_json(STATE_PATH)
    emails = load_json(EMAIL_DIGEST_PATH)
    events = load_upcoming_events(days=30)
    facts = load_facts()

    state_str = json.dumps(state, indent=2).replace('"John"', '"Jack"')[:8000]

    actionable = []
    if isinstance(emails, dict):
        items = emails.get("actionable") or emails.get("emails") or []
        if isinstance(items, list):
            actionable = [e for e in items if e.get("bucket") not in ("SKIP", "UNKNOWN")][:20]
    emails_str = json.dumps(actionable, indent=2)[:3000]

    events_str = "\n".join(
        "- " + e["date"] + ": " + e["title"] + (" (" + e["child"] + ")" if e.get("child") else "")
        for e in events
    ) or "No upcoming events in DB."

    facts_str = "\n".join(
        "- [" + f.get("subject", "?") + "] " + f.get("fact", "")
        for f in facts[:20]
    ) or "No facts recorded yet."

    today = date.today().strftime("%A, %B %d, %Y")

    return (
        "You are a helpful family assistant for the school dashboard.\n\n"
        "Answer questions about the children's grades, assignments, upcoming school events, and what needs attention.\n"
        f"Today: {today}\n\n"
        "Answer questions about grades, assignments, upcoming school events, and what needs attention. Be concise and practical.\n\n"
        f"=== UPCOMING SCHOOL EVENTS (next 30 days) ===\n{events_str}\n\n"
        f"=== KNOWN FACTS ===\n{facts_str}\n\n"
        f"=== SCHOOL STATE (grades, IXL, assignments) ===\n{state_str}\n\n"
        f"=== ACTIONABLE EMAILS ===\n{emails_str}\n"
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard-frame")
def dashboard_frame():
    try:
        with open(DASHBOARD_HTML) as f:
            return Response(f.read(), mimetype="text/html")
    except Exception as e:
        return Response(
            f"<html><body><h1>Dashboard not available</h1><p>{e}</p></body></html>",
            mimetype="text/html", status=500,
        )


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    if not message:
        return jsonify({"error": "empty message"}), 400

    messages = [{"role": "system", "content": build_system_prompt()}]
    for h in history[-10:]:
        role = h.get("role")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    try:
        resp = requests.post(
            f"{LITELLM_URL.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LITELLM_MODEL, "messages": messages, "max_tokens": 1500},
            timeout=60,
        )
        resp.raise_for_status()
        reply = resp.json()["choices"][0]["message"]["content"]
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/items", methods=["GET"])
def api_items_list():
    db_path = os.environ.get("SCHOOL_DB_PATH", "/opt/school/state/school.db")
    child = request.args.get("child") or None
    include_completed = request.args.get("include_completed", "0") == "1"
    try:
        from school_dashboard.db import init_db, list_items
        init_db(db_path)
        items = list_items(db_path, child=child, include_completed=include_completed)
        return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/items", methods=["POST"])
def api_items_create():
    db_path = os.environ.get("SCHOOL_DB_PATH", "/opt/school/state/school.db")
    data = request.get_json(silent=True) or {}
    child = (data.get("child") or "").strip()
    title = (data.get("title") or "").strip()
    if not child or not title:
        return jsonify({"error": "child and title are required"}), 400
    try:
        from school_dashboard.db import init_db, create_item, get_item
        init_db(db_path)
        item_id = create_item(
            db_path,
            child=child,
            title=title,
            item_type=data.get("type", "assignment"),
            source="manual",
            due_date=data.get("due_date") or None,
            notes=data.get("notes") or None,
        )
        item = get_item(db_path, item_id)
        if item is None:
            return jsonify({"error": "failed to retrieve created item"}), 500
        return jsonify(item), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/items/<int:item_id>", methods=["PATCH"])
def api_items_update(item_id):
    db_path = os.environ.get("SCHOOL_DB_PATH", "/opt/school/state/school.db")
    data = request.get_json(silent=True) or {}
    allowed = {"child", "title", "type", "due_date", "notes", "completed"}
    kwargs = {k: v for k, v in data.items() if k in allowed}
    if not kwargs:
        return jsonify({"error": "no valid fields provided"}), 400
    try:
        from school_dashboard.db import init_db, update_item
        init_db(db_path)
        changed = update_item(db_path, item_id, **kwargs)
        if not changed:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def api_items_delete(item_id):
    db_path = os.environ.get("SCHOOL_DB_PATH", "/opt/school/state/school.db")
    try:
        from school_dashboard.db import init_db, delete_item
        init_db(db_path)
        deleted = delete_item(db_path, item_id)
        if not deleted:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}

_CHILD_ALIASES: dict[str, str] = {
    "John": "Jack",
}

def _normalize_child(name: str) -> str:
    return _CHILD_ALIASES.get(name, name)


def _parse_due_iso(s: str | None) -> str | None:
    """Parse due string to ISO date. Handles:
    - Already-ISO: '2026-04-14'
    - Freeform: 'Due Tuesday, March 10, 2026 at 8:00 am'
    """
    if not s:
        return None
    # Already ISO format YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s.strip()):
        return s.strip()
    # Freeform: "March 10, 2026"
    m = re.search(r"(\w+)\s+(\d{1,2}),\s+(\d{4})", s)
    if m:
        month = _MONTHS.get(m.group(1))
        if month:
            return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"
    return None


@app.route("/api/dashboard")
def api_dashboard():
    try:
        state = load_json(STATE_PATH)
        if "error" in state:
            return jsonify({"error": state["error"]}), 500

        # Schoology: per-child assignment lists
        schoology: dict = {}
        for child, data in (state.get("schoology") or {}).items():
            name = _normalize_child(child)
            assignments = [
                {
                    "title": a.get("title", ""),
                    "course": a.get("course", ""),
                    "due_date": a.get("due_date", ""),
                    "status": a.get("status", ""),
                    "url": (SGY_BASE_URL.rstrip("/") + a["link"]) if a.get("link") else "",
                }
                for a in (data.get("assignments") or [])
            ]
            if assignments:
                schoology.setdefault(name, [])
                schoology[name].extend(assignments)

        # IXL: per-child subjects with remaining > 0
        ixl: dict = {}
        for child, data in (state.get("ixl") or {}).items():
            name = _normalize_child(child)
            subjects = [
                {
                    "subject": subj,
                    "remaining": vals.get("remaining", 0),
                    "assigned": vals.get("assigned", 0),
                    "done": vals.get("done", 0),
                }
                for subj, vals in (data.get("totals") or {}).items()
                if vals.get("remaining", 0) > 0
            ]
            if subjects:
                ixl[name] = subjects

        # Email action items only (with parsed due dates)
        email_items = [
            {
                "id": it.get("id", ""),
                "child": _normalize_child(it.get("child", "")),
                "source": it.get("source", ""),
                "summary": it.get("summary", ""),
                "due_iso": _parse_due_iso(it.get("due")),
                "due_raw": it.get("due") or "",
            }
            for it in (state.get("action_items") or [])
            if it.get("source") == "email"
        ]

        return jsonify({
            "schoology": schoology,
            "ixl": ixl,
            "email_items": email_items,
            "last_updated": state.get("last_updated", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendar")
def api_calendar():
    events = fetch_gcal_events(GOG_ACCOUNT)
    return jsonify({"events": events})


@app.route("/api/readiness")
def api_readiness():
    from school_dashboard.readiness import get_checklist
    try:
        checklist = get_checklist(STATE_PATH, DB_PATH)
        return jsonify({"checklist": checklist})
    except Exception as e:
        return jsonify({"error": str(e), "checklist": {}}), 500


@app.route("/api/digest/<digest_id>")
def api_digest_get(digest_id):
    from school_dashboard.db import init_digests_table, get_digest
    try:
        init_digests_table(DB_PATH)
        result = get_digest(DB_PATH, digest_id)
        if result is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/digest/<digest_id>/cards/<int:index>", methods=["PATCH"])
def api_digest_card_update(digest_id, index):
    from school_dashboard.db import init_digests_table, mark_digest_card_done
    data = request.get_json(silent=True) or {}
    done = data.get("done")
    if done is None:
        return jsonify({"error": "done field required"}), 400
    try:
        init_digests_table(DB_PATH)
        ok = mark_digest_card_done(DB_PATH, digest_id, index, bool(done))
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
