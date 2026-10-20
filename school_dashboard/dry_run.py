"""`school-state transition dry-run`: a strictly read-only year-transition report.

Reads never modify any file under the state dir: the SQLite DB is opened with
mode=ro (never init_db), a WAL-mode DB is read from a temporary copy, and there
is no network.
Missing inputs become entries in `gaps`, never errors or created files.
The report never includes environment values or file contents.
"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

from school_dashboard import maintenance, paths
from school_dashboard import state as _state

OVERDUE_ARCHIVE_DAYS = 30
RECENT_BACKUP_DAYS = 7
SCHOOL_YEAR_START_MONTH = 8
DEFAULT_IXL_DIR = "/tmp/ixl"
DEFAULT_SGY_FILE = "/tmp/schoology-daily.json"
OWNER_NOTE = "Proposals only: the family owner decides every carry/archive/review outcome."
_YEAR_RE = re.compile(r"^(\d{4})-(\d{2})$")


class DryRunArgError(ValueError):
    """Invalid dry-run arguments (CLI exit 2)."""


# ── years ────────────────────────────────────────────────────────────────────

def _start_year(label: str, flag: str) -> int:
    m = _YEAR_RE.match(label or "")
    if not m:
        raise DryRunArgError(f"{flag} must look like YYYY-YY (e.g. 2026-27), got {label!r}")
    start = int(m.group(1))
    if int(m.group(2)) != (start + 1) % 100:
        raise DryRunArgError(f"{flag} {label!r}: end year must be start year + 1")
    return start


def default_from_year(today: date) -> str:
    start = today.year if today.month >= SCHOOL_YEAR_START_MONTH else today.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def resolve_years(to_year: str, from_year: Optional[str], today: date) -> dict:
    to_start = _start_year(to_year, "--to-year")
    from_label = from_year or default_from_year(today)
    derived = "" if from_year else " (derived from today's date)"
    from_start = _start_year(from_label, "--from-year")
    if to_start != from_start + 1:
        raise DryRunArgError(
            f"--to-year {to_year} must immediately follow --from-year, which is {from_label}{derived}; "
            "pass --from-year explicitly if that is not the current school year")
    return {"from": from_label, "to": to_year}


# ── read-only readers ────────────────────────────────────────────────────────

def _read_json(p: Path) -> tuple:
    """(data, problem) where problem is None, 'missing' or 'unreadable'."""
    try:
        return json.loads(p.read_text()), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError):
        return None, "unreadable"


def _is_wal(p: Path) -> bool:
    """True when the DB header (bytes 18-19) says WAL mode. Plain read-only file open."""
    with open(p, "rb") as f:
        header = f.read(20)
    return len(header) == 20 and header[18] == 2 and header[19] == 2


def _connect_ro(p: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


@contextmanager
def _open_ro(p: Path) -> Iterator[sqlite3.Connection]:
    """Read-only connection that never creates or modifies files beside the real DB.

    A WAL-mode DB cannot be opened in place without touching -wal/-shm, so it is
    read from a private temporary copy (db + -wal + -shm) that is always removed.
    """
    if not _is_wal(p):
        conn = _connect_ro(p)
        try:
            yield conn
        finally:
            conn.close()
        return
    with tempfile.TemporaryDirectory(prefix="dry-run-") as tmp:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(p) + suffix)
            if src.exists():
                shutil.copyfile(src, Path(tmp) / (p.name + suffix))
        conn = _connect_ro(Path(tmp) / p.name)
        try:
            yield conn
        finally:
            conn.close()


def _read_db(p: Path) -> dict:
    empty = {"problem": None, "detail": None, "items": [], "digests": 0, "events_table": False, "events": 0}
    if not p.exists():
        return {**empty, "problem": "missing"}
    try:
        with _open_ro(p) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            items = _select_items(conn) if "items" in tables else []
            count = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
            return {**empty, "items": items,
                    "digests": count("digests") if "digests" in tables else 0,
                    "events_table": "events" in tables,
                    "events": count("events") if "events" in tables else 0}
    except (sqlite3.Error, OSError) as exc:
        return {**empty, "problem": "unreadable", "detail": f"{type(exc).__name__}: {exc}"}


def _select_items(conn: sqlite3.Connection) -> list:
    cur = conn.execute("SELECT child, title, due_date, source, completed FROM items")
    return [{"store": "sqlite", "child": r[0], "title": r[1], "due": r[2],
             "source": r[3] or "manual", "done": bool(r[4])} for r in cur]


def _state_records(state: dict) -> list:
    return [{"store": "state", "child": i.get("child"), "title": i.get("summary") or "",
             "due": i.get("due"), "source": i.get("source") or "manual",
             "done": i.get("status") == "completed"}
            for i in state.get("action_items", []) if isinstance(i, dict)]


# ── children ─────────────────────────────────────────────────────────────────

def _resolver(roster: list) -> Callable[[str], tuple]:
    known = {}
    for c in roster:
        known[c["name"].lower()] = c["id"]
        known[c["id"].lower()] = c["id"]

    def resolve(raw: Optional[str]) -> tuple:
        """(child_id, canonical_name, configured)."""
        canon = _state._canonicalize(raw or "")
        cid = known.get(canon.lower())
        return (cid, canon, True) if cid else (_state._slug(canon) or "unknown", canon, False)
    return resolve


def _names_with_data(section: object, resolve: Callable) -> set:
    return {resolve(n)[0] for n in section} if isinstance(section, dict) else set()


def _children_report(roster: list, state: dict, resolve: Callable, records: list) -> dict:
    ixl = _names_with_data(state.get("ixl"), resolve)
    sgy = _names_with_data(state.get("schoology"), resolve)
    seen = [resolve(n) for n in list(state.get("ixl") or {}) + list(state.get("schoology") or {})]
    seen += [resolve(r["child"]) for r in records]
    unconfigured = sorted({canon for _, canon, ok in seen if not ok})
    return {
        "roster": [{**c, "ixl_data": c["id"] in ixl, "schoology_data": c["id"] in sgy} for c in roster],
        "unconfigured_children": unconfigured,
    }


# ── counts ───────────────────────────────────────────────────────────────────

def _tally(records: list, resolve: Callable) -> dict:
    def bucket(key: Callable) -> dict:
        c = Counter((key(r), "completed" if r["done"] else "open") for r in records)
        return {k: {"open": c[(k, "open")], "completed": c[(k, "completed")]}
                for k in sorted({k for k, _ in c})}
    return {
        "open": sum(1 for r in records if not r["done"]),
        "completed": sum(1 for r in records if r["done"]),
        "by_child": bucket(lambda r: resolve(r["child"])[0]),
        "by_source": bucket(lambda r: r["source"]),
    }


def _gc_counts(gc: object) -> dict:
    teams = gc.get("teams", []) if isinstance(gc, dict) else []
    events = sum(len(t.get("schedule") or []) for t in teams if isinstance(t, dict))
    return {"teams": len(teams), "events": events}


def _sync_meta_report(meta: object, now: datetime) -> dict:
    out = {}
    for src, v in (meta.items() if isinstance(meta, dict) else []):
        if not isinstance(v, dict):
            continue
        out[src] = {"last_run": v.get("last_run"), "last_result": v.get("last_result"),
                    "age_hours": _age_hours(v.get("last_run"), now)}
    return out


def _age_hours(stamp: object, now: datetime) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    dt = dt if dt.tzinfo else dt.astimezone()  # naive stamps are local time
    return round((now - dt).total_seconds() / 3600, 1)


# ── carryover ────────────────────────────────────────────────────────────────

def _norm(title: str) -> str:
    return " ".join((title or "").lower().split())


def _parse_due(due: object) -> Optional[date]:
    try:
        return date.fromisoformat(str(due)[:10])
    except ValueError:
        return None


def _treatment(group: list, today: date) -> str:
    if all(r["done"] for r in group):
        return "retain-history"
    dues = [d for d in (_parse_due(r["due"]) for r in group if not r["done"]) if d]
    open_dues = [r for r in group if not r["done"]]
    if not dues or len(dues) != len(open_dues):
        return "review"
    return "archive-candidate" if today - max(dues) > timedelta(days=OVERDUE_ARCHIVE_DAYS) else "carry"


def _group_entry(key: tuple, group: list, today: date, configured: bool) -> dict:
    stores = [r["store"] for r in group]
    flags = []
    if len(group) > 1:
        flags.append("duplicate")
    if len({r["done"] for r in group}) > 1:
        flags.append("status-conflict")
    dues = sorted({str(r["due"]) for r in group if r["due"]})
    return {"child_id": key[0], "title": key[1], "source": key[2], "stores": sorted(set(stores)),
            "due": dues[0] if dues else None, "treatment": _treatment(group, today),
            "flags": flags, "child_configured": configured}


def _collisions(groups: dict) -> list:
    by_title: dict = {}
    for (cid, title, src), group in groups.items():
        by_title.setdefault((cid, title), []).append((src, group))
    out = []
    for (cid, title), parts in sorted(by_title.items()):
        sources = sorted({s for s, _ in parts})
        dues = sorted({str(r["due"]) for _, g in parts for r in g if r["due"]})
        if len(sources) > 1 or len(dues) > 1:
            out.append({"child_id": cid, "title": title, "sources": sources, "dues": dues,
                        "risk": "same title may belong to different courses/instances"})
    return out


def _carryover(records: list, resolve: Callable, today: date) -> dict:
    groups: dict = {}
    configured: dict = {}
    for r in records:
        cid, _, ok = resolve(r["child"])
        key = (cid, _norm(r["title"]), r["source"])
        groups.setdefault(key, []).append(r)
        configured[key] = ok
    entries = [_group_entry(k, g, today, configured[k]) for k, g in sorted(groups.items())]
    return {
        "unresolved": [e for e in entries
                       if e["treatment"] != "retain-history"],
        "retain_history": sum(1 for e in entries if e["treatment"] == "retain-history"),
        "collisions": _collisions(groups),
        "note": OWNER_NOTE,
    }


# ── memberships, stale inputs, archive, backups ──────────────────────────────

def _team_map() -> dict:
    pairs = (p.split(":", 1) for p in os.environ.get("GC_TEAM_MAP", "").split(",") if ":" in p)
    return {k.strip(): v.strip() for k, v in pairs}


def _memberships(gc: object, resolve: Callable) -> list:
    teams = gc.get("teams", []) if isinstance(gc, dict) else []
    tmap = _team_map()
    out = []
    for t in (t for t in teams if isinstance(t, dict)):
        tid = str(t.get("team_id", ""))
        cid, _, ok = resolve(tmap.get(tid) or t.get("child") or "")
        mapped = bool(tmap.get(tid) or t.get("child")) and ok
        out.append({"team_id": tid, "team_name": t.get("team_name"), "child_id": cid if mapped else None,
                    "status": ("continuing (never archived by school-year boundary)"
                               if mapped else "unmapped")})
    return out


def _file_info(p: Path, cutoff: datetime) -> dict:
    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    return {"name": p.name, "modified": mtime.isoformat(), "older_than_from_year_start": mtime < cutoff}


def _stale_inputs(from_start: int) -> dict:
    cutoff = datetime(from_start, SCHOOL_YEAR_START_MONTH, 1, tzinfo=timezone.utc)
    ixl_dir = Path(os.environ.get("IXL_DIR", DEFAULT_IXL_DIR))
    sgy = Path(os.environ.get("SGY_FILE", DEFAULT_SGY_FILE))
    files = [_file_info(p, cutoff) for p in sorted(ixl_dir.glob("*.json")) if p.is_file()] \
        if ixl_dir.is_dir() else []
    sgy_info = _file_info(sgy, cutoff) if sgy.is_file() else None
    return {
        "risk": "stale scraper input could refill new year" if files or sgy_info else None,
        "ixl_dir": {"path": str(ixl_dir), "files": files},
        "sgy_file": {"path": str(sgy), "present": sgy_info is not None, **(sgy_info or {})},
        "email_note": "Gmail scan label state (OpenClaw/Scanned) is not touched by the dry run.",
    }


def _archive_report(dest: Optional[Path], years: dict) -> dict:
    dest = dest or paths.state_dir() / "archive" / f"{years['from']}-to-{years['to']}"
    parent = dest.parent
    anc = parent
    while not anc.exists() and anc != anc.parent:
        anc = anc.parent
    writable = anc.is_dir() and os.access(anc, os.W_OK | os.X_OK)
    return {"dest": str(dest), "exists": dest.exists(), "parent_exists": parent.exists(),
            "parent_writable": parent.exists() and os.access(parent, os.W_OK | os.X_OK),
            "creatable": writable, "valid": not dest.exists() and writable}


def _backups_report(now: datetime) -> dict:
    stamps = []
    for m in sorted((paths.state_dir() / "backups").glob("*/manifest.json")):
        data, problem = _read_json(m)
        if problem is None and isinstance(data, dict) and isinstance(data.get("created_at"), str):
            stamps.append(data["created_at"])
    latest = max(stamps, default=None)
    age = _age_hours(latest, now) if latest else None
    return {"latest": latest, "recent": age is not None and age <= RECENT_BACKUP_DAYS * 24}


# ── gaps ─────────────────────────────────────────────────────────────────────

def _gap(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _gaps(r: dict, problems: dict) -> list:
    g = []
    if problems["db"]:
        g.append(_gap(f"db-{problems['db']}", f"SQLite database is {problems['db']}"
                      + (f" ({problems['detail']})" if problems["detail"] else "")))
    elif not r["stores"]["events"]["table_exists"]:
        g.append(_gap("events-table-missing", "events table does not exist in the DB"))
    if problems["state"]:
        g.append(_gap(f"state-{problems['state']}", f"school-state.json is {problems['state']}"))
    if not r["roster"]:
        g.append(_gap("roster-empty", "no children configured in the roster"))
    if r["unconfigured_children"]:
        g.append(_gap("unconfigured-children", "children in data but not in roster: "
                      + ", ".join(r["unconfigured_children"])))
    g += [_gap("gc-team-unmapped", f"GameChanger team {m['team_id']} has no child mapping")
          for m in r["memberships"] if m["status"] == "unmapped"]
    if not paths.calendar_pdf_path().exists():
        g.append(_gap("no-calendar-source", "no official calendar source configured"))
    g.append(_gap("no-activity-inventory", "no activity inventory beyond GameChanger"))
    if not r["maintenance_active"]:
        g.append(_gap("pruning-not-suspended", "maintenance mode is not active; retention pruning is not suspended"))
    if not r["backups"]["recent"]:
        g.append(_gap("no-recent-backup", f"no backup in the last {RECENT_BACKUP_DAYS} days under state/backups"))
    if not r["archive"]["valid"]:
        g.append(_gap("archive-dest-invalid", "archive destination exists or cannot be created"))
    if r["stale_inputs"]["risk"]:
        g.append(_gap("stale-scraper-input", r["stale_inputs"]["risk"]))
    return g


# ── entry points ─────────────────────────────────────────────────────────────

def build_report(to_year: str, from_year: Optional[str] = None, archive_dest: Optional[Path] = None,
                 today: Optional[date] = None, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    years = resolve_years(to_year, from_year, today)
    roster = _state.get_roster()
    resolve = _resolver(roster)
    st, st_problem = _read_json(paths.state_path())
    st = st if isinstance(st, dict) else {}
    dbr = _read_db(paths.db_path())
    state_recs = _state_records(st)
    records = dbr["items"] + state_recs
    facts, _ = _read_json(paths.facts_path())
    gc, _ = _read_json(paths.gc_path())
    meta, _ = _read_json(paths.sync_meta_path())
    report = {
        "years": years,
        **_children_report(roster, st, resolve, records),
        "stores": {
            "sqlite_items": _tally(dbr["items"], resolve),
            "digests": dbr["digests"],
            "events": {"table_exists": dbr["events_table"], "count": dbr["events"]},
            "state_action_items": _tally(state_recs, resolve),
            "facts": len(facts) if isinstance(facts, list) else 0,
            "gc": _gc_counts(gc),
            "sync_meta": _sync_meta_report(meta, now),
            "email_digest_present": paths.email_digest_path().exists(),
        },
        "carryover": _carryover(records, resolve, today),
        "memberships": _memberships(gc, resolve),
        "stale_inputs": _stale_inputs(int(years["from"][:4])),
        "archive": _archive_report(archive_dest, years),
        "backups": _backups_report(now),
        "maintenance_active": maintenance.is_active(),
    }
    return {**report, "gaps": _gaps(report, {"db": dbr["problem"], "detail": dbr["detail"], "state": st_problem})}


# ── text rendering ───────────────────────────────────────────────────────────

def _tally_line(label: str, t: dict) -> str:
    kids = ", ".join(f"{k}={v['open']}/{v['completed']}" for k, v in t["by_child"].items())
    srcs = ", ".join(f"{k}={v['open']}/{v['completed']}" for k, v in t["by_source"].items())
    return f"  {label}: open={t['open']} completed={t['completed']}\n    by child (open/done): {kids or '-'}\n    by source: {srcs or '-'}"


def _render_stores(r: dict) -> list:
    s = r["stores"]
    meta = ", ".join(f"{k}={v['last_result']} ({v['age_hours']}h)" for k, v in s["sync_meta"].items())
    return ["Record counts",
            _tally_line("SQLite items", s["sqlite_items"]),
            _tally_line("State action items", s["state_action_items"]),
            f"  digests={s['digests']} events={s['events']['count']} (table exists: {s['events']['table_exists']})",
            f"  facts={s['facts']} gc teams={s['gc']['teams']} gc events={s['gc']['events']}",
            f"  sync freshness: {meta or 'none'}",
            f"  email-digest.json present: {s['email_digest_present']}"]


def _render_carryover(r: dict) -> list:
    c = r["carryover"]
    lines = [f"Carryover proposals ({len(c['unresolved'])} unresolved, {c['retain_history']} completed kept as history)"]
    for e in c["unresolved"]:
        flags = f" [{', '.join(e['flags'])}]" if e["flags"] else ""
        lines.append(f"  {e['treatment']:<17} {e['child_id']}/{e['source']}: {e['title']} "
                     f"(due {e['due'] or 'none'}; {'+'.join(e['stores'])}){flags}")
    lines += [f"  RISK collision: {x['child_id']} '{x['title']}' sources={x['sources']} dues={x['dues']}"
              for x in c["collisions"]]
    return lines + [f"  {c['note']}"]


def render_text(r: dict) -> str:
    y = r["years"]
    lines = [f"Transition dry run: {y['from']} -> {y['to']} (read-only; nothing was changed)", "", "Roster"]
    lines += [f"  {c['id']}: {c['name']} grade {c['grade']} {c['school']} "
              f"(ixl data: {c['ixl_data']}, schoology data: {c['schoology_data']})" for c in r["roster"]]
    lines.append(f"  unconfigured children in data: {', '.join(r['unconfigured_children']) or 'none'}")
    lines += [""] + _render_stores(r) + [""] + _render_carryover(r) + ["", "Activity memberships"]
    lines += [f"  {m['team_id']} {m['team_name']}: {m['status']}" for m in r["memberships"]] or ["  none"]
    st = r["stale_inputs"]
    lines += ["", "Scraper inputs", f"  {st['risk'] or 'no leftover scraper input found'}",
              f"  ixl dir {st['ixl_dir']['path']}: {len(st['ixl_dir']['files'])} file(s); "
              f"sgy file present: {st['sgy_file']['present']}", f"  {st['email_note']}"]
    a = r["archive"]
    lines += ["", f"Archive destination: {a['dest']} (exists: {a['exists']}, creatable: {a['creatable']}, valid: {a['valid']})",
              "", f"Gaps / blockers ({len(r['gaps'])})"]
    lines += [f"  - [{g['code']}] {g['message']}" for g in r["gaps"]]
    return "\n".join(lines) + "\n"
