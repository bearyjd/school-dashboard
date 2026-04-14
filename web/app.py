import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import requests
from flask import Flask, jsonify, render_template, request, Response, send_from_directory
from school_dashboard.gcal import fetch_gcal_events
from school_dashboard.sync_meta import write_sync_source, read_sync_meta, DEFAULT_PATH as SYNC_META_DEFAULT_PATH

app = Flask(__name__)

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:8080")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "cliproxy/claude-sonnet-4-6")
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


def _format_freshness(meta: dict) -> str:
    """Format per-source sync freshness for LLM system prompt."""
    now = datetime.now(timezone.utc)
    lines = []
    for source in ("ixl", "sgy", "gc"):
        entry = meta.get(source)
        if not entry:
            lines.append(f"{source.upper()}: never pulled")
            continue
        try:
            last = datetime.fromisoformat(entry["last_run"])
            delta = now - last
            days = delta.days
            if days == 0:
                hours = delta.seconds // 3600
                age = f"today ({hours}h ago)" if hours > 0 else "just now"
            elif days == 1:
                age = "yesterday"
            else:
                age = f"{days} days ago"
        except (KeyError, ValueError, TypeError):
            age = "unknown"
        result_str = entry.get("last_result", "?")
        lines.append(f"{source.upper()}: last pulled {age} — {result_str}")
    return "\n".join(lines)


def build_system_prompt() -> str:
    meta_path = os.environ.get("SCHOOL_SYNC_META_PATH", SYNC_META_DEFAULT_PATH)
    meta = read_sync_meta(meta_path)
    freshness_str = _format_freshness(meta)

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
        f"## Data Freshness\n{freshness_str}\n\n"
        f"=== UPCOMING SCHOOL EVENTS (next 30 days) ===\n{events_str}\n\n"
        f"=== KNOWN FACTS ===\n{facts_str}\n\n"
        f"=== SCHOOL STATE (grades, IXL, assignments) ===\n{state_str}\n\n"
        f"=== ACTIONABLE EMAILS ===\n{emails_str}\n"
    )


@app.route("/")
def index():
    return render_template("index.html", sync_token=os.environ.get("SYNC_TOKEN", ""))


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


def _build_inline_context(context_type: str, context_id: str) -> tuple[str, list[str]]:
    """Return (context_description, available_actions) for the inline agent."""
    db_path = os.environ.get("SCHOOL_DB_PATH", "/opt/school/state/school.db")

    if context_type == "item":
        from school_dashboard.db import init_db, get_item
        init_db(db_path)
        try:
            item_id = int(context_id)
        except ValueError:
            raise ValueError(f"context_id must be a valid integer for context_type 'item', got {context_id!r}")
        item = get_item(db_path, item_id)
        if item is None:
            raise ValueError(f"item {context_id} not found")
        safe_title = (item.get('title') or '').replace('\n', ' ').replace('\r', ' ')
        safe_notes = (item.get('notes') or 'none').replace('\n', ' ').replace('\r', ' ')
        ctx = (
            f"Homework item for {item['child']}: '{safe_title}', "
            f"type={item['type']}, due={item.get('due_date') or 'unset'}, "
            f"completed={bool(item['completed'])}, notes={safe_notes}"
        )
        return ctx, ["mark_item_done", "reschedule_item", "create_item"]

    if context_type == "sync_source":
        meta_path = os.environ.get("SCHOOL_SYNC_META_PATH", SYNC_META_DEFAULT_PATH)
        meta = read_sync_meta(meta_path)
        entry = meta.get(context_id, {})
        ctx = (
            f"Sync source '{context_id}': "
            f"last_run={entry.get('last_run') or 'never'}, "
            f"last_result={entry.get('last_result') or 'unknown'}"
        )
        return ctx, ["trigger_sync"]

    raise ValueError(f"unknown context_type: {context_type!r}")


@app.route("/api/agent/inline", methods=["POST"])
def api_agent_inline():
    data = request.get_json(silent=True) or {}
    context_type = (data.get("context_type") or "").strip()
    context_id = (data.get("context_id") or "").strip()
    message = (data.get("message") or "").strip()
    if not context_type or not context_id or not message:
        return jsonify({"error": "context_type, context_id, and message are required"}), 400

    try:
        context_str, available_actions = _build_inline_context(context_type, context_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    system = (
        "You are a focused assistant for a family school dashboard.\n"
        f"Context: {context_str}\n"
        f"Available actions: {', '.join(available_actions)}\n"
        "If you want to take an action, end your reply with exactly one line:\n"
        "ACTION: <action_type> <json_payload>\n"
        "Otherwise reply with plain helpful text. Be brief (1-3 sentences)."
    )

    try:
        resp = requests.post(
            f"{LITELLM_URL.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LITELLM_MODEL, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ], "max_tokens": 400},
            timeout=30,
        )
        resp.raise_for_status()
        reply_text = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Only check the last non-empty line for ACTION (prevents mid-reply injection)
    action: dict | None = None
    lines = reply_text.strip().split("\n")
    if lines and lines[-1].strip().startswith("ACTION:"):
        try:
            rest = lines[-1].strip()[len("ACTION:"):].strip()
            action_type, payload_str = rest.split(" ", 1)
            action = {"type": action_type, "payload": json.loads(payload_str)}
            clean_lines = lines[:-1]
        except Exception:
            action = None
            clean_lines = lines
    else:
        clean_lines = lines

    return jsonify({"reply": "\n".join(clean_lines).strip(), "action": action})


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


# ── On-demand sync ───────────────────────────────────────────────────────────

_sync_lock = threading.Lock()
_sync_status: dict = {
    "running": False,
    "last_run": None,
    "last_result": None,
    "last_sources": [],
    "last_error": None,
}


def _run_sync_background(sources: str, digest: str) -> None:
    """Run scrapers in a background thread, update state, optionally send digest."""
    global _sync_status
    _sync_status["running"] = True
    _sync_status["last_sources"] = [s.strip() for s in sources.split(",")]
    _sync_status["last_error"] = None

    ixl_dir = os.environ.get("IXL_DIR", "/tmp/ixl")
    sgy_file = os.environ.get("SGY_FILE", "/tmp/schoology-daily.json")
    ixl_cron = os.environ.get("IXL_CRON", "")
    state_path = os.environ.get("SCHOOL_STATE_PATH", "/app/state/school-state.json")
    db_path = os.environ.get("SCHOOL_DB_PATH", "/app/state/school.db")
    facts_path = os.environ.get("SCHOOL_FACTS_PATH", "/app/state/facts.json")
    gc_path = os.environ.get("SCHOOL_GC_PATH", "/app/state/gc-schedule.json")
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")

    source_list = [s.strip() for s in sources.split(",")]
    if "all" in source_list:
        source_list = ["ixl", "sgy", "gc"]

    errors = []

    try:
        for src in source_list:
            try:
                if src == "ixl":
                    if ixl_cron and Path(ixl_cron).exists():
                        subprocess.run(["bash", ixl_cron], timeout=120, check=False)
                    else:
                        Path(ixl_dir).mkdir(parents=True, exist_ok=True)
                        result = subprocess.run(
                            ["ixl", "summary", "--json"],
                            capture_output=True, text=True, timeout=120,
                        )
                        if result.stdout:
                            (Path(ixl_dir) / "ixl-summary.json").write_text(result.stdout)
                elif src == "sgy":
                    result = subprocess.run(
                        ["sgy", "summary", "--json"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if result.stdout:
                        Path(sgy_file).write_text(result.stdout)
                elif src == "gc":
                    gc_script = "/app/sync/gc-scrape.sh"
                    if Path(gc_script).exists():
                        subprocess.run(["bash", gc_script], timeout=60, check=False)
            except Exception as exc:
                errors.append(f"{src}: {exc}")
                write_sync_source(src, "error")
            else:
                write_sync_source(src, "ok")

        # Update state (only if ixl or sgy were synced)
        if any(s in source_list for s in ("ixl", "sgy")):
            subprocess.run(
                ["school-state", "update", "--ixl-dir", ixl_dir, "--sgy-file", sgy_file],
                timeout=30, check=False,
            )
            subprocess.run(["school-state", "html"], timeout=30, check=False)

        # Digest
        if ntfy_topic and digest != "none":
            if digest == "quick":
                from school_dashboard.digest import build_quick_check, send_ntfy
                text, cards = build_quick_check(state_path)
                send_ntfy(topic=ntfy_topic, message=text, title="Homework Check",
                          cards=cards or None, db_path=db_path if cards else None)
            elif digest == "full":
                from school_dashboard.digest import build_afternoon_digest, send_ntfy
                litellm_url = os.environ.get("LITELLM_URL", "")
                api_key = os.environ.get("LITELLM_API_KEY", "")
                model = os.environ.get("LITELLM_MODEL", "claude-sonnet")
                if litellm_url:
                    text, cards = build_afternoon_digest(
                        state_path=state_path, db_path=db_path,
                        litellm_url=litellm_url, api_key=api_key, model=model,
                        gc_path=gc_path,
                    )
                    send_ntfy(topic=ntfy_topic, message=text, title="Homework Check",
                              cards=cards, db_path=db_path)

        _sync_status["last_result"] = "ok" if not errors else "error"
        if errors:
            _sync_status["last_error"] = "; ".join(errors)
    except Exception as exc:
        _sync_status["last_result"] = "error"
        _sync_status["last_error"] = str(exc)
    finally:
        _sync_status["running"] = False
        _sync_status["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _sync_lock.release()


@app.route("/api/sync", methods=["POST"])
def api_sync():
    sync_token = os.environ.get("SYNC_TOKEN", "")
    if not sync_token:
        return jsonify({"error": "SYNC_TOKEN not configured"}), 501

    provided = request.headers.get("X-Sync-Token", "")
    if not provided or provided != sync_token:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    sources = (data.get("sources") or "ixl,sgy").strip()
    digest = (data.get("digest") or "quick").strip()

    if not _sync_lock.acquire(blocking=False):
        return jsonify({"error": "sync already running"}), 409

    t = threading.Thread(target=_run_sync_background, args=(sources, digest), daemon=True)
    t.start()
    return jsonify({"started": True, "sources": sources, "digest": digest}), 202


@app.route("/api/sync/status")
def api_sync_status():
    return jsonify(dict(_sync_status))


@app.route("/api/sync/meta")
def api_sync_meta():
    meta_path = os.environ.get("SCHOOL_SYNC_META_PATH", SYNC_META_DEFAULT_PATH)
    return jsonify(read_sync_meta(meta_path))


@app.route("/.well-known/assetlinks.json")
def assetlinks():
    """TWA domain verification — fingerprint populated when signing APK."""
    package = os.environ.get("TWA_PACKAGE_NAME", "cc.grepon.school")
    fingerprint = os.environ.get("TWA_CERT_FINGERPRINT", "PLACEHOLDER_REPLACE_WITH_APK_SIGNING_CERT_SHA256")
    return jsonify([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": package,
            "sha256_cert_fingerprints": [fingerprint],
        },
    }])


# ── SPA (React build served at /app) ─────────────────────────────────────────

_SPA_DIST = Path(__file__).parent / "spa" / "dist"


@app.route("/app/", defaults={"path": ""})
@app.route("/app/<path:path>")
def spa(path: str):
    """Serve the React SPA — inject sync token and serve index.html for all routes."""
    sync_token = os.environ.get("SYNC_TOKEN", "")
    index = _SPA_DIST / "index.html"
    if not index.exists():
        return "SPA not built. Run: npm --prefix web/spa run build", 503
    html = index.read_text()
    # Inject SYNC_TOKEN as a global so the SPA can read it without a separate API call
    safe_token = json.dumps(sync_token).replace("</", r"<\/")
    injection = f'<script>window.__SYNC_TOKEN__={safe_token};</script>'
    html = html.replace("</head>", f"{injection}</head>", 1)
    return Response(html, mimetype="text/html")


@app.route("/app/assets/<path:filename>")
def spa_assets(filename: str):
    return send_from_directory(_SPA_DIST / "assets", filename)


@app.route("/app/icons/<path:filename>")
def spa_icons(filename: str):
    return send_from_directory(_SPA_DIST / "icons", filename)


@app.route("/app/manifest.json")
def spa_manifest():
    return send_from_directory(_SPA_DIST, "manifest.json", mimetype="application/manifest+json")


@app.route("/app/sw.js")
def spa_sw():
    return send_from_directory(_SPA_DIST, "sw.js", mimetype="application/javascript")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
