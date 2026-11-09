import hmac
import json
import os
import re
import signal
import sqlite3
import subprocess
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from flask import Flask, jsonify, redirect, render_template, request, Response, send_from_directory
from school_dashboard.gcal import fetch_gcal_events
from school_dashboard.llm import chat_completion
from school_dashboard.scrape_meta import data_warnings_block as scrape_data_warnings, gc_warnings, powerschool_warnings, scrape_warnings
from school_dashboard.state import canonical_name, get_roster, ixl_payload_ok
from school_dashboard import csp, db, maintenance, paths, powerschool
from school_dashboard.csp import CSP_REPORT_ONLY  # noqa: F401  (re-exported for tests and docs)
from school_dashboard.rate_limit import FailureLimiter, client_key, trusted_client_ip, trusted_proxy_count
from school_dashboard.sources import source_enabled
from school_dashboard.sync_meta import write_sync_source, read_sync_meta

app = Flask(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_MAINTENANCE_RETRY_AFTER = "60"
# /api/chat only reads state and calls the LLM; it persists nothing.
# /api/csp-report only counts browser reports; it persists nothing.
_MAINTENANCE_EXEMPT = frozenset({"/api/chat", csp.REPORT_PATH})
csp.init_app(app)


@app.before_request
def _block_writes_in_maintenance():
    """Reject API writes with 503 while the maintenance flag is active."""
    if (request.method in _SAFE_METHODS or not request.path.startswith("/api/")
            or request.path in _MAINTENANCE_EXEMPT):
        return None
    info = maintenance.status()
    if info is None:
        return None
    app.logger.info("Maintenance active (%s); rejected %s %s",
                    info.get("reason", ""), request.method, request.path)
    resp = jsonify({"error": "maintenance"})
    resp.status_code = 503
    resp.headers["Retry-After"] = _MAINTENANCE_RETRY_AFTER
    return resp

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:8080")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "school-dash")
GOG_ACCOUNT = os.environ.get("GOG_ACCOUNT", "")
SGY_BASE_URL = os.environ.get("SGY_BASE_URL", "https://arlingtondiocese.schoology.com")


def load_json(path: str) -> dict | list:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"could not load {path}: {e}"}


def load_upcoming_events(days: int = 30) -> list[dict]:
    if not paths.db_path().exists():
        return []
    try:
        from_date = date.today().isoformat()
        end_date = (date.today() + timedelta(days=days)).isoformat()
        conn = db.open_conn(str(paths.db_path()))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT date, title, type, child FROM events WHERE date >= ? AND date < ? ORDER BY date",
            (from_date, end_date),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except (sqlite3.Error, OSError) as exc:
        if isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc):
            app.logger.debug("Events table not present in the school DB")
        else:
            app.logger.warning("Failed to load upcoming events from the school DB", exc_info=True)
        return []


def load_facts() -> list[dict]:
    try:
        p = paths.facts_path()
        if p.exists():
            return json.loads(p.read_text())
    except (OSError, ValueError):
        app.logger.warning("Failed to load facts file", exc_info=True)
    return []


def _format_freshness(meta: dict) -> str:
    """Format per-source sync freshness for LLM system prompt."""
    now = datetime.now(timezone.utc)
    lines = []
    for source in ("ixl", "sgy", "gc", "powerschool"):
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
        if result_str == "paused":
            lines.append(f"{source.upper()}: paused (last pulled {age})")
            continue
        lines.append(f"{source.upper()}: last pulled {age} — {result_str}")
    return "\n".join(lines)


def build_system_prompt() -> str:
    meta_path = paths.sync_meta_path()
    meta = read_sync_meta(meta_path)
    freshness_str = _format_freshness(meta)

    state = load_json(str(paths.state_path()))
    emails = load_json(str(paths.email_digest_path()))
    events = load_upcoming_events(days=30)
    facts = load_facts()

    ps_block = powerschool.format_block(state if isinstance(state, dict) else {})
    ps_str = (
        f"=== MISSING / LATE / DUE SOON (PowerSchool; {powerschool.UNTRUSTED_NOTE}) ===\n{ps_block}\n\n"
        if ps_block else ""
    )
    hidden = ("scrape_meta", "powerschool", "powerschool_meta") if ps_block else ("scrape_meta",)
    state_view = (
        {k: v for k, v in state.items() if k not in hidden} if isinstance(state, dict) else state
    )
    state_str = json.dumps(state_view, indent=2).replace('"John"', '"Jack"')[:8000]
    # The state JSON is cut at 8000 chars, so scrape problems are stated outside it.
    warn_block = (scrape_data_warnings(state, gc_data=load_json(str(paths.gc_path())))
                  if isinstance(state, dict) else "")
    warn_str = f"{warn_block}\n\n" if warn_block else ""

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
        f"{warn_str}=== SCHOOL STATE (grades, IXL, assignments) ===\n{state_str}\n\n"
        f"{ps_str}"
        f"=== ACTIONABLE EMAILS ===\n{emails_str}\n"
    )


@app.route("/")
def index():
    """Land on the SPA. 302, not 301, so browsers never cache the redirect.

    ntfy digests link to ``/?digest=<id>`` and only the pre-SPA page can render that
    carousel, so those links go to ``/legacy`` until the SPA has a digest view.
    """
    query = request.query_string.decode("utf-8", errors="replace")
    target = "/legacy" if "digest" in request.args else "/app/"
    return redirect(target + (f"?{query}" if query else ""), code=302)


@app.route("/legacy")
def legacy_index():
    """The pre-SPA dashboard, kept until the classic view is retired."""
    return render_template("index.html")


@app.route("/dashboard-frame")
def dashboard_frame():
    try:
        with open(paths.dashboard_html_path()) as f:
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
        reply = chat_completion(
            messages,
            LITELLM_URL,
            LITELLM_API_KEY,
            LITELLM_MODEL,
            max_tokens=1500,
            timeout=60,
        )
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _build_inline_context(context_type: str, context_id: str) -> tuple[str, list[str]]:
    """Return (context_description, available_actions) for the inline agent."""
    db_path = str(paths.db_path())

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
        meta_path = paths.sync_meta_path()
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
        reply_text = chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
            LITELLM_URL,
            LITELLM_API_KEY,
            LITELLM_MODEL,
            max_tokens=400,
            timeout=30,
        )
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
        except ValueError:  # includes json.JSONDecodeError and a missing payload on split
            app.logger.warning("Ignoring malformed ACTION line in model reply")
            action = None
            clean_lines = lines
    else:
        clean_lines = lines

    return jsonify({"reply": "\n".join(clean_lines).strip(), "action": action})


@app.route("/api/items", methods=["GET"])
def api_items_list():
    db_path = str(paths.db_path())
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
    db_path = str(paths.db_path())
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
    db_path = str(paths.db_path())
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
    db_path = str(paths.db_path())
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

def _normalize_child(name: str | None) -> str:
    return canonical_name(name)


def _dict_items(section: object):
    """(key, value) pairs of a state section, skipping non-dict values (e.g. scraper error strings)."""
    if not isinstance(section, dict):
        return []
    return [(k, v) for k, v in section.items() if isinstance(v, dict)]


def _has_data_flags(state: dict) -> dict:
    """Per-child {ixl, sgy, ps} flags: True only if the child has a non-empty entry in state."""
    def populated(section: object) -> set:
        if not isinstance(section, dict):
            return set()
        return {_normalize_child(k) for k, v in section.items() if isinstance(v, dict) and v}

    ixl_keys = populated(state.get("ixl"))
    sgy_keys = populated(state.get("schoology"))
    ps_keys = populated(state.get("powerschool"))
    roster_names = {r["name"] for r in get_roster()}
    names = [r["name"] for r in get_roster()] + sorted((ixl_keys | sgy_keys | ps_keys) - roster_names)
    return {n: {"ixl": n in ixl_keys, "sgy": n in sgy_keys, "ps": n in ps_keys} for n in names}


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
        state = load_json(str(paths.state_path()))
        if "error" in state:
            return jsonify({"error": state["error"]}), 500

        # Schoology: per-child assignment lists
        schoology: dict = {}
        for child, data in _dict_items(state.get("schoology")):
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
                if isinstance(a, dict)
            ]
            if assignments:
                schoology.setdefault(name, [])
                schoology[name].extend(assignments)

        # IXL: per-child subjects with remaining > 0
        ixl: dict = {}
        for child, data in _dict_items(state.get("ixl")):
            name = _normalize_child(child)
            subjects = [
                {
                    "subject": subj,
                    "remaining": vals.get("remaining", 0),
                    "assigned": vals.get("assigned", 0),
                    "done": vals.get("done", 0),
                }
                for subj, vals in _dict_items(data.get("totals"))
                if vals.get("remaining", 0) > 0
            ]
            if subjects:
                ixl[name] = subjects

        # PowerSchool: per-child missing / late / upcoming lists
        ps: dict = {}
        for child, data in _dict_items(state.get("powerschool")):
            name = _normalize_child(child)
            lists = {key: powerschool.clean_assignments(data.get(key)) for key in ("missing", "late", "upcoming")}
            ps[name] = {**lists, "upcoming": powerschool.drop_past_upcoming(lists["upcoming"]),
                        "updated": data.get("updated", "")}

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
            if isinstance(it, dict) and it.get("source") == "email"
        ]

        return jsonify({
            "schoology": schoology,
            "ixl": ixl,
            "powerschool": ps,
            "powerschool_warnings": list((state.get("powerschool_meta") or {}).get("warnings") or []),
            "scrape_warnings": scrape_warnings(state, datetime.now(timezone.utc)),
            "children": get_roster(),
            "has_data": _has_data_flags(state),
            "email_items": email_items,
            "last_updated": state.get("last_updated", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _int_arg(name: str, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


@app.route("/api/overview")
def api_overview():
    from school_dashboard import overview, overview_events, overview_items, watchdog
    from school_dashboard.db import list_items

    days = _int_arg("days", 14, 1, 60)
    include_past = request.args.get("past") == "1"
    now = datetime.now(timezone.utc)
    tz = watchdog.get_tz()
    warnings: list[str] = []

    state = load_json(str(paths.state_path()))
    if not isinstance(state, dict) or "error" in state:
        state = {}
        warnings.append("school state unavailable")
    else:
        warnings.extend(scrape_warnings(state, now))
        warnings.extend(powerschool_warnings(state))
    gc_data = load_json(str(paths.gc_path()))
    if not isinstance(gc_data, dict) or "error" in gc_data:
        gc_data = {}
    warnings.extend(gc_warnings(gc_data, clean=True))
    db_file = str(paths.db_path())
    try:
        manual = list_items(db_file)
    except sqlite3.Error:
        manual = []
        warnings.append("manual items unavailable")
    today = overview_items.local_today(now, tz)
    school = overview_events.school_events(db_file, today, days)
    payload = overview.build_overview(
        state, manual, gc_data, school, read_sync_meta(str(paths.sync_meta_path())),
        get_roster(), now, tz, days=days, include_past_seasons=include_past,
        active=watchdog.monitored_sources(), first_seen=watchdog.load_first_seen())
    return jsonify({**payload, "warnings": warnings + payload["warnings"]})


@app.route("/api/calendar")
def api_calendar():
    events = fetch_gcal_events(GOG_ACCOUNT)
    return jsonify({"events": events})


@app.route("/api/readiness")
def api_readiness():
    from school_dashboard.readiness import get_checklist
    try:
        checklist = get_checklist(str(paths.state_path()), str(paths.db_path()))
        return jsonify({"checklist": checklist})
    except Exception as e:
        return jsonify({"error": str(e), "checklist": {}}), 500


@app.route("/api/digest/<digest_id>")
def api_digest_get(digest_id):
    from school_dashboard.db import init_digests_table, get_digest
    try:
        init_digests_table(str(paths.db_path()))
        result = get_digest(str(paths.db_path()), digest_id)
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
        init_digests_table(str(paths.db_path()))
        ok = mark_digest_card_done(str(paths.db_path()), digest_id, index, bool(done))
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
GENERIC_SYNC_ERROR = "Sync failed. See the server log for details."


def _record_sync_error(detail: str) -> None:
    """Clients get generic text via /api/sync/status; the detail goes to the server log only."""
    app.logger.error("Sync error: %s", detail)
    _sync_status["last_error"] = GENERIC_SYNC_ERROR


def _sync_paused_for_maintenance() -> bool:
    """True (and status updated) when maintenance is active, so the sync must stop."""
    if not maintenance.is_active():
        return False
    _sync_status["last_result"] = "skipped: maintenance"
    return True


STATE_COMMAND_TIMEOUT = 120  # must exceed statelock.LOCK_TIMEOUT (60 s)


def _run_state_command(cmd: list, errors: list) -> None:
    """Run a school-state command; a timeout is recorded as a sync error instead of aborting the sync."""
    try:
        subprocess.run(cmd, timeout=STATE_COMMAND_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        errors.append(f"{' '.join(cmd[:2])}: timed out after {STATE_COMMAND_TIMEOUT}s")


SYNC_SOURCES = frozenset({"ixl", "sgy", "gc", "powerschool", "all"})


def _scrape_powerschool(ps_file: str) -> None:
    """Run `powerschool summary --json`; write the file only on success (atomic replace)."""
    result = subprocess.run(
        ["powerschool", "summary", "--json"], capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"powerschool summary failed (exit {result.returncode})")
    problem = powerschool.scrape_problem(result.stdout)
    if problem:
        raise RuntimeError(f"powerschool summary unusable: {problem}")
    with tempfile.NamedTemporaryFile(
        "w", dir=str(Path(ps_file).parent), prefix=Path(ps_file).name + ".", delete=False
    ) as tmp:
        tmp.write(result.stdout)
    try:
        os.chmod(tmp.name, 0o600)
        os.replace(tmp.name, ps_file)
    except OSError:
        Path(tmp.name).unlink(missing_ok=True)
        raise


def _run_gc_scrape(script: str) -> None:
    """Run gc-scrape.sh; raise on missing script, non-zero exit or timeout.

    The script runs in its own process group so a timeout can SIGTERM the whole
    group (the script's TERM trap then removes its work dir) before SIGKILL.
    """
    if not Path(script).exists():
        raise FileNotFoundError(f"gc scrape script not found: {script}")
    timeout = float(os.environ.get("GC_SCRAPE_TIMEOUT", "300"))
    proc = subprocess.Popen(["bash", script], start_new_session=True)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        raise
    if rc != 0:
        raise subprocess.CalledProcessError(rc, script)


def _send_sync_digest(digest: str, ntfy_topic: str, state_path: str,
                      db_path: str, gc_path: str) -> None:
    """Push the post-sync digest ("quick" or "full"); raises on build/delivery failure."""
    if digest == "quick":
        from school_dashboard.digest import build_quick_check, send_ntfy
        text, cards = build_quick_check(state_path)
        send_ntfy(topic=ntfy_topic, message=text, title="Homework Check",
                  cards=cards or None, db_path=db_path if cards else None)
    elif digest == "full":
        from school_dashboard.digest import build_afternoon_digest, send_ntfy
        litellm_url = os.environ.get("LITELLM_URL", "")
        if litellm_url:
            text, cards = build_afternoon_digest(
                state_path=state_path, db_path=db_path, litellm_url=litellm_url,
                api_key=os.environ.get("LITELLM_API_KEY", ""),
                model=os.environ.get("LITELLM_MODEL", "claude-sonnet"),
                gc_path=gc_path,
            )
            send_ntfy(topic=ntfy_topic, message=text, title="Homework Check",
                      cards=cards, db_path=db_path)


def _run_sync_background(sources: str, digest: str) -> None:
    """Run scrapers in a background thread, update state, optionally send digest."""
    global _sync_status
    _sync_status["running"] = True
    _sync_status["last_sources"] = [s.strip() for s in sources.split(",")]
    _sync_status["last_error"] = None

    # Marker first, then the maintenance check: a backup that sees no marker
    # knows no sync can have passed the check before it.
    marker = maintenance.mark_sync_running()
    if marker is None:
        app.logger.error("Sync marker could not be written; a concurrent backup may not wait for this sync")
    if _sync_paused_for_maintenance():
        _sync_status["running"] = False
        _sync_status["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        maintenance.clear_sync_running(marker)
        _sync_lock.release()
        return

    ixl_dir = os.environ.get("IXL_DIR", "/tmp/ixl")
    sgy_file = os.environ.get("SGY_FILE", "/tmp/schoology-daily.json")
    ps_file = os.environ.get("PS_FILE", "/tmp/powerschool-daily.json")
    ixl_cron = os.environ.get("IXL_CRON", "")
    state_path = str(paths.state_path())
    db_path = str(paths.db_path())
    gc_path = str(paths.gc_path())
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")

    source_list = [s.strip() for s in sources.split(",")]
    if "all" in source_list:
        source_list = ["ixl", "sgy", "gc", "powerschool"]

    errors = []

    try:
        for src in source_list:
            if _sync_paused_for_maintenance():
                return
            if not source_enabled(src):
                write_sync_source(src, "paused")
                continue
            try:
                if src == "ixl":
                    if ixl_cron and Path(ixl_cron).exists():
                        subprocess.run(["bash", ixl_cron], timeout=120, check=False)
                    else:
                        Path(ixl_dir).mkdir(parents=True, exist_ok=True)
                        files_written = 0
                        failed = 0

                        def _scrape_ixl_child(name: str, child_env: dict) -> bool:
                            """Write <name>-assigned.json only for a clean, non-error scrape;
                            otherwise remove the old file so stale data is not refreshed."""
                            target = Path(ixl_dir) / f"{name}-assigned.json"
                            timeout = float(os.environ.get("IXL_SCRAPE_TIMEOUT", "120"))
                            ok, detail = False, ""
                            try:
                                r = subprocess.run(
                                    ["ixl", "assigned", "--json"],
                                    capture_output=True, text=True, timeout=timeout, env=child_env,
                                )
                                detail = f"exit {r.returncode}"
                                ok = r.returncode == 0 and ixl_payload_ok(json.loads(r.stdout))
                                if ok:
                                    target.write_text(r.stdout)
                            except subprocess.TimeoutExpired:
                                detail = "timed out"
                            except (OSError, ValueError) as exc:
                                detail = type(exc).__name__
                                ok = False
                            if ok:
                                return True
                            target.unlink(missing_ok=True)
                            app.logger.warning("IXL scrape failed for %s (%s)", name, detail)
                            return False

                        accounts_env = Path.home() / ".ixl" / "accounts.env"
                        if accounts_env.exists():
                            for line in accounts_env.read_text().splitlines():
                                line = line.strip()
                                if not line or line.startswith("#"):
                                    continue
                                parts = line.split(":", 2)
                                if len(parts) < 3:
                                    continue
                                child_env = {**os.environ, "IXL_EMAIL": parts[1].strip(), "IXL_PASSWORD": parts[2].strip()}
                                if _scrape_ixl_child(parts[0].strip().lower(), child_env):
                                    files_written += 1
                                else:
                                    failed += 1
                        else:
                            ixl_email = os.environ.get("IXL_EMAIL", "student@school")
                            child_name = ixl_email.split("@")[0].split(".")[0].lower()
                            if _scrape_ixl_child(child_name, dict(os.environ)):
                                files_written += 1
                            else:
                                failed += 1
                        if files_written == 0:
                            raise RuntimeError("ixl assigned produced no output")
                        if failed:
                            raise RuntimeError(f"ixl assigned failed for {failed} child(ren)")
                elif src == "sgy":
                    result = subprocess.run(
                        ["sgy", "summary", "--json"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if result.stdout:
                        Path(sgy_file).write_text(result.stdout)
                elif src == "powerschool":
                    reason = powerschool.skip_reason()
                    if reason:
                        app.logger.info("powerschool sync skipped: %s", reason)
                        continue
                    _scrape_powerschool(ps_file)
                elif src == "gc":
                    _run_gc_scrape(os.environ.get("GC_SCRAPE_SCRIPT", "/app/sync/gc-scrape.sh"))
            except Exception as exc:
                errors.append(f"{src}: {exc}")
                write_sync_source(src, "error")
            else:
                write_sync_source(src, "ok")

        # Update state (only if ixl, sgy or powerschool were synced)
        if any(s in source_list and source_enabled(s) for s in ("ixl", "sgy", "powerschool")):
            if _sync_paused_for_maintenance():
                return
            _run_state_command(["school-state", "update", "--ixl-dir", ixl_dir, "--sgy-file", sgy_file,
                                "--ps-file", ps_file], errors)
            if _sync_paused_for_maintenance():
                return
            _run_state_command(["school-state", "html"], errors)

        # Digest
        if ntfy_topic and digest != "none":
            if _sync_paused_for_maintenance():
                return
            try:
                _send_sync_digest(digest, ntfy_topic, state_path, db_path, gc_path)
            except Exception:
                app.logger.error("Sync digest delivery failed", exc_info=True)
                errors.append("digest delivery failed")

        all_paused = bool(source_list) and not any(source_enabled(s) for s in source_list)
        _sync_status["last_result"] = "error" if errors else "ok (all paused)" if all_paused else "ok"
        if errors:
            _record_sync_error("; ".join(errors))
    except Exception as exc:
        _sync_status["last_result"] = "error"
        _record_sync_error(str(exc))
    finally:
        _sync_status["running"] = False
        _sync_status["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        maintenance.clear_sync_running(marker)
        _sync_lock.release()


# Bad X-Sync-Token attempts per client (normalised remote_addr; X-Forwarded-For only with TRUSTED_PROXY_COUNT > 0).
_sync_failures = FailureLimiter(max_failures=10, window=60.0)
MIN_SYNC_TOKEN_LEN = 32
_weak_token_logged = False


def _configured_sync_token() -> str:
    """SYNC_TOKEN, or "" when unset or shorter than MIN_SYNC_TOKEN_LEN (treated as not configured)."""
    global _weak_token_logged
    token = os.environ.get("SYNC_TOKEN", "")
    if token and len(token) < MIN_SYNC_TOKEN_LEN:
        if not _weak_token_logged:
            _weak_token_logged = True
            app.logger.warning("SYNC_TOKEN ignored: it must be at least %d characters; /api/sync is disabled",
                               MIN_SYNC_TOKEN_LEN)
        return ""
    return token


@app.route("/api/sync", methods=["POST"])
def api_sync():
    sync_token = _configured_sync_token()
    if not sync_token:
        return jsonify({"error": "SYNC_TOKEN not configured"}), 501

    client = client_key(trusted_client_ip(
        request.remote_addr, ", ".join(request.headers.getlist("X-Forwarded-For")),
        trusted_proxy_count(os.environ.get("TRUSTED_PROXY_COUNT"))))
    provided = request.headers.get("X-Sync-Token", "")
    if provided and hmac.compare_digest(provided.encode(), sync_token.encode()):
        _sync_failures.reset(client)  # a correct token is never blocked by someone else's failures
    else:
        wait = _sync_failures.fail(client)
        if wait:
            resp = jsonify({"error": "too many attempts"})
            resp.status_code = 429
            resp.headers["Retry-After"] = str(wait)
            return resp
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    raw_sources = data.get("sources") or "ixl,sgy,powerschool"
    if not isinstance(raw_sources, str):
        return jsonify({"error": "sources must be a comma-separated string"}), 400
    requested = [s.strip() for s in raw_sources.split(",") if s.strip()]
    unknown = [s for s in requested if s not in SYNC_SOURCES]
    if unknown or not requested:
        return jsonify({"error": f"unknown source(s): {', '.join(unknown) or '(none given)'}; "
                                 f"allowed: {', '.join(sorted(SYNC_SOURCES))}"}), 400
    sources = ",".join(requested)
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
    meta_path = paths.sync_meta_path()
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
    """Serve the React SPA index.html for all routes. SYNC_TOKEN is never rendered here."""
    index = _SPA_DIST / "index.html"
    if not index.exists():
        return "SPA not built. Run: npm --prefix web/spa run build", 503
    return Response(index.read_text(), mimetype="text/html")


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
