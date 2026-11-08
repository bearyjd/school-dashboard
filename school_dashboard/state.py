import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from school_dashboard import maintenance, paths, powerschool
from school_dashboard import scrape_meta as scrape
from school_dashboard.atomic import write_atomic

DEFAULT_CONFIG_PATH = Path("/etc/school-dashboard/config.json")

PRUNE_COMPLETED_AFTER_DAYS = 7
PRUNE_PAST_DUE_AFTER_DAYS = 3

logger = logging.getLogger(__name__)

_config_cache: Optional[dict] = None


def _config_path() -> Path:
    env = os.environ.get("SCHOOL_DASHBOARD_CONFIG")
    if env:
        return Path(env)
    return DEFAULT_CONFIG_PATH


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _validate_child(name: str, entry: object, seen_ids: set) -> Optional[dict]:
    """Return a new validated roster dict (with id filled), or None if invalid."""
    if not isinstance(entry, dict):
        logger.warning("Config child %r skipped: entry must be an object", name)
        return None
    for field in ("grade", "school"):
        if not isinstance(entry.get(field), str):
            logger.warning("Config child %r skipped: %r must be a string", name, field)
            return None
    child_id = entry.get("id") or _slug(name)
    if not isinstance(child_id, str) or not child_id:
        logger.warning("Config child %r skipped: invalid id", name)
        return None
    if child_id in seen_ids:
        logger.warning("Config child %r skipped: duplicate id %r", name, child_id)
        return None
    return {"id": child_id, "name": name, "grade": entry["grade"], "school": entry["school"]}


def _dict_entries(children: object) -> dict:
    """Keep only object-valued children (lenient); log anything dropped."""
    if not isinstance(children, dict):
        logger.warning("Config 'children' must be an object; ignoring")
        return {}
    for name, entry in children.items():
        if not isinstance(entry, dict):
            logger.warning("Config child %r skipped: entry must be an object", name)
    return {k: v for k, v in children.items() if isinstance(v, dict)}


def _read_config(p: Path) -> Optional[dict]:
    """Return the parsed config, or None if it could not be loaded."""
    try:
        raw = json.loads(p.read_text())
    except FileNotFoundError:
        if os.environ.get("SCHOOL_DASHBOARD_CONFIG"):
            logger.warning("Config file %s not found", p)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load config %s: %s", p, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("Config %s must be a JSON object; ignoring", p)
        return None
    children = raw.get("children", {})
    aliases = raw.get("name_aliases", {})
    return {
        **raw,
        "children": _dict_entries(children),
        "name_aliases": aliases if isinstance(aliases, dict) else {},
    }


def _load_config() -> dict:
    """Load config; only a successful load is cached so a fixed file is picked up."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    config = _read_config(_config_path())
    if config is None:
        return {"children": {}, "name_aliases": {}}
    _config_cache = config
    return config


def get_children() -> dict:
    return _load_config().get("children", {})


def get_roster() -> list:
    """Return the strictly validated roster as a new list of {id, name, grade, school}."""
    roster: list = []
    seen_ids: set = set()
    for name, entry in get_children().items():
        child = _validate_child(name, entry, seen_ids)
        if child is not None:
            seen_ids.add(child["id"])
            roster.append(child)
    return roster


def ixl_opted_out() -> set:
    """Canonical names of roster children configured with `"ixl": false` (left IXL).

    Only a real bool counts; anything else is warned about and treated as the default (true).
    """
    out: set = set()
    for name, entry in get_children().items():
        flag = entry.get("ixl", True)
        if not isinstance(flag, bool):
            logger.warning("Config child %r: 'ixl' must be true or false; ignoring %r", name, flag)
        elif not flag:
            out.add(canonical_name(name))
    return out


# Built-in default alias (lowercase key). Config `name_aliases` entries override it.
_DEFAULT_NAME_ALIASES: dict = {"john": "Jack"}


def get_name_aliases() -> dict:
    return {**_DEFAULT_NAME_ALIASES, **_load_config().get("name_aliases", {})}


def canonical_name(name: str | None) -> str:
    """Canonical child name: alias lookup (config over built-in), else title-cased."""
    if not name or not name.strip():
        return ""
    return get_name_aliases().get(name.strip().lower(), name.strip().title())


_canonicalize = canonical_name


def _empty_state() -> dict:
    return {
        "last_updated": None,
        "children": get_children(),
        "ixl": {},
        "schoology": {},
        "powerschool": {},
        "action_items": [],
        "calendar_events_created": [],
        "email_last_scan": None,
    }


def _state_path(override: Optional[str] = None) -> Path:
    if override:
        return Path(override)
    return paths.state_path()


def load(path: Optional[str] = None) -> dict:
    p = _state_path(path)
    if not p.exists():
        return _empty_state()
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty_state()


class StateLoadError(RuntimeError):
    pass


def load_strict(path: Optional[str] = None) -> dict:
    """Like load(), but an existing state file that cannot be parsed raises StateLoadError.

    A missing file still yields an empty state. Use for load -> modify -> save cycles so a
    corrupt file is never silently replaced by an empty one.
    """
    p = _state_path(path)
    if not p.exists():
        return _empty_state()
    try:
        data = json.loads(p.read_text())
    except (ValueError, OSError) as exc:
        raise StateLoadError(f"state file {p} exists but cannot be read ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise StateLoadError(f"state file {p} exists but is not a JSON object")
    return data


def save(state: dict, path: Optional[str] = None) -> Path:
    p = _state_path(path)
    if maintenance.is_active():
        logger.warning("Maintenance mode active; not saving state to %s", p)
        return p
    state["last_updated"] = datetime.now().isoformat()
    try:
        mode = p.stat().st_mode & 0o777
    except OSError:
        mode = 0o600
    write_atomic(p, json.dumps(state, indent=2), mode)
    return p


def update_ixl(state: dict, child_name: str, assigned_data: dict) -> None:
    """Merge IXL assigned JSON into state for one child.

    assigned_data is the output of `ixl assigned --json`:
      {"totals": {...}, "remaining": [...]}
    """
    state["ixl"][child_name] = {
        "updated": datetime.now().isoformat(),
        "totals": assigned_data.get("totals", {}),
        "remaining": assigned_data.get("remaining", []),
    }


def update_schoology(state: dict, child_name: str, child_data: dict) -> None:
    """Merge SGY per-child data into state.

    child_data is one entry from `sgy summary --json`.per_child[]:
      {"child": {...}, "assignments": [...], "grades": [...], "announcements": [...]}
    """
    state["schoology"][child_name] = {
        "updated": datetime.now().isoformat(),
        "assignments": child_data.get("assignments", []),
        "grades": _compact_grades(child_data.get("grades", [])),
        "announcements": child_data.get("announcements", [])[:10],
    }


def _compact_grades(grades: list) -> list:
    """Keep course-level grades, drop per-assignment detail items to save space."""
    return [
        {
            "course": g.get("course", ""),
            "grade": g.get("grade", ""),
            "letter": g.get("letter", ""),
        }
        for g in grades
    ]


def _action_id(source: str, summary: str, child: str) -> str:
    raw = f"{source}:{child}:{summary}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def add_action_item(
    state: dict,
    child: str,
    source: str,
    item_type: str,
    summary: str,
    due: Optional[str] = None,
) -> dict:
    aid = _action_id(source, summary, child)

    for existing in state["action_items"]:
        if existing["id"] == aid:
            if due and not existing.get("due"):
                existing["due"] = due
            return existing

    item = {
        "id": aid,
        "child": child,
        "source": source,
        "type": item_type,
        "summary": summary,
        "due": due,
        "status": "pending",
        "created": datetime.now().isoformat(),
    }
    state["action_items"].append(item)
    return item


def complete_action_item(state: dict, action_id: str) -> bool:
    for item in state["action_items"]:
        if item["id"] == action_id:
            item["status"] = "completed"
            item["completed_at"] = datetime.now().isoformat()
            return True
    return False


def prune_stale(state: dict) -> int:
    if maintenance.is_active():
        return 0
    now = datetime.now()
    pruned = 0
    kept = []

    for item in state["action_items"]:
        should_prune = False

        if item["status"] == "completed":
            completed_at = item.get("completed_at", item.get("created", ""))
            try:
                dt = datetime.fromisoformat(completed_at)
                if (now - dt).days > PRUNE_COMPLETED_AFTER_DAYS:
                    should_prune = True
            except (ValueError, TypeError):
                should_prune = True

        if item.get("due") and item["status"] == "pending":
            try:
                due_dt = datetime.fromisoformat(item["due"][:10])
                if (now - due_dt).days > PRUNE_PAST_DUE_AFTER_DAYS:
                    should_prune = True
            except (ValueError, TypeError):
                pass

        if should_prune:
            pruned += 1
        else:
            kept.append(item)

    state["action_items"] = kept
    return pruned


def ixl_payload_ok(data: object) -> bool:
    """True for a real `ixl assigned --json` payload; False for empty/error-shaped output."""
    return (
        isinstance(data, dict)
        and data.get("status") != "error"
        and not data.get("errors")
        and ("totals" in data or "remaining" in data)
    )


IXL_TOTALS_KEYS = ("assigned", "done", "remaining")


def _is_count(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def ixl_totals_problem(data: dict) -> Optional[str]:
    """Why a (shape-valid) IXL payload cannot be trusted as a count of remaining work, else None.

    `totals` must exist and every subject must carry numeric assigned/done/remaining; an empty
    `totals` object is a legitimate "nothing assigned".
    """
    totals = data.get("totals")
    if not isinstance(totals, dict):
        return "totals missing"
    for subject in totals.values():
        if not isinstance(subject, dict) or not all(_is_count(subject.get(k)) for k in IXL_TOTALS_KEYS):
            return "totals incomplete"
    return None


def _read_ixl_file(f: Path) -> tuple:
    """(payload, reason): reason is a scrape_meta code when the file cannot be used, else None."""
    try:
        data = json.loads(f.read_text())
    except OSError:
        return None, scrape.FAILED
    except ValueError:
        return None, scrape.UNEXPECTED
    if not ixl_payload_ok(data):
        logger.warning("Skipping %s: empty or error-shaped IXL payload", f.name)
        failed = isinstance(data, dict) and (data.get("status") == "error" or data.get("errors"))
        return None, scrape.FAILED if failed else scrape.UNEXPECTED
    problem = ixl_totals_problem(data)
    if problem:
        logger.warning("Not applying %s: IXL %s", f.name, problem)
        return None, scrape.UNEXPECTED
    return data, None


def update_from_ixl_files(state: dict, ixl_dir: str = "/tmp/ixl") -> int:
    """Read all {name}-assigned.json files from ixl output dir and merge into state.

    A file that is unreadable, not JSON, error-shaped, lacks usable totals, or has empty totals
    after a non-empty entry leaves that child's previous data untouched. A missing directory or
    no files at all is a whole-source problem. Both are recorded in state["scrape_meta"]["ixl"].
    """
    ixl_path = Path(ixl_dir)
    files = sorted(ixl_path.glob("*-assigned.json")) if ixl_path.is_dir() else []
    unavailable: dict = {}
    count = 0
    for f in files:
        child_name = _canonicalize(f.stem.replace("-assigned", ""))
        data, reason = _read_ixl_file(f)
        if data is not None and not data["totals"] and ((state.get("ixl") or {}).get(child_name) or {}).get("totals"):
            data, reason = None, scrape.EMPTY
            logger.warning("Not applying %s: IXL totals are empty after earlier data", f.name)
        if data is None:
            unavailable[child_name] = reason
            continue
        update_ixl(state, child_name, data)
        count += 1
    if not files:
        unavailable[scrape.ANY_CHILD] = scrape.MISSING
    else:
        # school-sync.sh deletes a failed child's file: a roster child that had data but has no
        # file now is unavailable. One that never had IXL data is not (no account, no alarm).
        seen = {_canonicalize(f.stem.replace("-assigned", "")) for f in files}
        opted_out = ixl_opted_out()
        for r in get_roster():
            name = _canonicalize(r["name"])
            if name not in seen and name not in opted_out and (state.get("ixl") or {}).get(name):
                unavailable[name] = scrape.MISSING
    scrape.set_unavailable(state, "ixl", unavailable)
    return count


def _sgy_child_name(entry: object) -> str:
    """First name word of a per_child entry, "" when the entry or its name is unusable."""
    info = entry.get("child") if isinstance(entry, dict) else None
    name = info.get("name") if isinstance(info, dict) else None
    parts = name.split() if isinstance(name, str) else []
    return parts[0] if parts else ""


def sgy_assignments_problem(entry: dict) -> Optional[str]:
    """Why a child's assignments cannot be trusted (a renamed/missing `title` or `due_date` key), else None.

    The scraper always emits both keys on every assignment (`due_date` may be empty). Without
    `title` every item would collapse into one "Unknown"; without `due_date` dated rows vanish.
    """
    assignments = entry.get("assignments", [])
    if not isinstance(assignments, list):
        return "assignments is not a list"
    for a in assignments:
        if not isinstance(a, dict):
            return "assignment is not an object"
        if not isinstance(a.get("title"), str):
            return "assignment without a title"
        if "due_date" not in a:
            return "assignment without a due_date"
    return None


def update_from_sgy_file(state: dict, sgy_file: str = "/tmp/schoology-daily.json") -> int:
    """Read sgy summary --json output and merge into state.

    A child whose scrape_confidence is "failed", a missing/non-list `per_child`, or an empty
    `per_child` while state already holds schoology data leaves previous data untouched and is
    recorded in state["scrape_meta"]["schoology"]. None of these raises; `update` still exits 0.
    """
    p = Path(sgy_file)
    unavailable: dict = {}
    per_child: list = []
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        data = None
        unavailable[scrape.ANY_CHILD] = scrape.MISSING
    except OSError:
        data = None
        unavailable[scrape.ANY_CHILD] = scrape.FAILED
    except ValueError:
        data = None
        unavailable[scrape.ANY_CHILD] = scrape.UNEXPECTED
    if data is not None:
        raw_children = data.get("per_child") if isinstance(data, dict) else None
        if not isinstance(raw_children, list):
            unavailable[scrape.ANY_CHILD] = scrape.UNEXPECTED
        elif not raw_children and state.get("schoology"):
            unavailable[scrape.ANY_CHILD] = scrape.EMPTY
        else:
            per_child = raw_children

    count = 0
    for entry in per_child:
        raw_name = _sgy_child_name(entry)
        if not raw_name:
            unavailable[scrape.UNNAMED] = scrape.NONAME
            logger.warning("Schoology scrape: a child entry has no usable name; skipped")
            continue
        name = _canonicalize(raw_name)
        if entry.get("scrape_confidence") == "failed":
            unavailable[name] = scrape.FAILED
            continue
        problem = sgy_assignments_problem(entry)
        if problem:
            unavailable[name] = scrape.UNEXPECTED
            logger.warning("Schoology scrape: not applying a child entry: %s", problem)
            continue
        update_schoology(state, name, entry)

        for assignment in entry.get("assignments", []):
            due = assignment.get("due_date", "")
            add_action_item(
                state,
                child=name,
                source="schoology",
                item_type="assignment",
                summary=assignment.get("title", "Unknown"),
                due=due if due else None,
            )
        count += 1

    scrape.set_unavailable(state, "schoology", unavailable)
    return count


def _read_ps_scrape(p: Path, now: datetime) -> tuple:
    """(data, scrape_time, problem): data is the usable scrape dict, else None with a reason."""
    try:
        data = json.loads(p.read_text())
    except OSError:
        return None, None, "missing" if not p.exists() else f"unreadable: {p}"
    except ValueError:
        return None, None, "malformed JSON"
    if not isinstance(data, dict):
        return None, None, "not a JSON object"
    children = data.get("children")
    if not isinstance(children, list) or not children:
        return None, None, "no children in scrape"
    if data.get("warnings"):
        return None, None, "scraper reported warnings: " + "; ".join(str(w) for w in data["warnings"])[:200]
    stamp = powerschool.scrape_time(data.get("scraped_at"), p.stat().st_mtime)
    if (stamp - now).total_seconds() > powerschool.MAX_FUTURE_SECONDS:
        return None, None, f"scraped_at is in the future ({stamp.isoformat()})"
    age = (now - stamp).total_seconds() / 3600
    if age > powerschool.MAX_SCRAPE_AGE_HOURS:
        return None, None, f"stale ({age:.0f}h old)"
    return data, stamp, ""


def _ps_child_entry(entry: dict, stamp: datetime) -> Optional[dict]:
    """New sanitised entry, or None unless missing, late and upcoming are all lists."""
    keys = ("missing", "late", "upcoming")
    if not all(isinstance(entry.get(k), list) for k in keys):
        return None
    return {"updated": stamp.isoformat(), **{k: powerschool.clean_assignments(entry[k]) for k in keys}}


def _ps_names(children: list) -> list:
    """[(canonical first name, entry)] for well-formed child entries."""
    pairs = []
    for entry in children:
        first = powerschool.first_name(entry.get("name")) if isinstance(entry, dict) else ""
        if first:
            pairs.append((_canonicalize(first), entry))
    return pairs


def _merge_ps_children(section: dict, children: list, stamp: datetime) -> tuple:
    """Merge trusted child entries into section; returns (count, warnings)."""
    pairs = _ps_names(children)
    counts: dict = {}
    for name, _ in pairs:
        counts[name] = counts.get(name, 0) + 1
    roster = {_canonicalize(c["name"]).casefold() for c in get_roster()}
    warnings: list = []
    merged = 0
    for name, entry in pairs:
        if roster and name.casefold() not in roster:
            warnings.append(f"PowerSchool child '{name}' not in roster; add name_aliases")
            continue
        if counts[name] > 1:
            warnings.append(f"PowerSchool {name}: ambiguous ({counts[name]} children share this first name), skipped")
            continue
        if entry.get("warnings"):
            warnings.append(f"PowerSchool {name}: kept previous data ({'; '.join(map(str, entry['warnings']))[:200]})")
            continue
        new_entry = _ps_child_entry(entry, stamp)
        if new_entry is None:
            warnings.append(f"PowerSchool {name}: malformed lists, kept previous data")
            continue
        section[name] = new_entry
        merged += 1
    return merged, sorted(set(warnings))


def update_from_ps_file(
    state: dict, ps_file: str = "/tmp/powerschool-daily.json", now: Optional[datetime] = None
) -> int:
    """Read `powerschool summary --json` output and merge into state["powerschool"].

    An untrusted scrape (missing/stale/future/empty/malformed file, or top-level warnings)
    leaves previous data untouched and records a warning in state["powerschool_meta"]. On a
    trusted scrape a child with warnings or malformed lists keeps its previous entry; any
    other child entry replaces it wholesale. Children absent from trusted scrapes for
    CHILD_EXPIRY_DAYS are dropped.
    """
    now = now or datetime.now()
    section = state.setdefault("powerschool", {})
    data, stamp, problem = _read_ps_scrape(Path(ps_file), now)
    if data is None:
        if problem != "missing" or section:
            state["powerschool_meta"] = {"warnings": [f"PowerSchool scrape not applied: {problem}"]}
            logger.warning("PowerSchool scrape not applied (%s): %s", problem, ps_file)
        return 0

    count, warnings = _merge_ps_children(section, data["children"], stamp)
    for name in [n for n, e in section.items() if powerschool.is_expired(e.get("updated"), now)]:
        del section[name]
    state["powerschool_meta"] = {"warnings": warnings}
    return count


def pending_action_items(state: dict, child: Optional[str] = None) -> list:
    items = [i for i in state["action_items"] if i["status"] == "pending"]
    if child:
        items = [i for i in items if i["child"].lower() == child.lower()]
    return sorted(items, key=lambda x: x.get("due") or "9999")


def summary_text(state: dict) -> str:
    lines = []
    updated = state.get("last_updated", "never")
    lines.append(f"State updated: {updated}")
    lines.append("")

    for name, info in state.get("children", {}).items():
        lines.append(f"{name} ({info['grade']}, {info['school']})")

        ixl = state.get("ixl", {}).get(name, {})
        if scrape.unavailable_for(state, "ixl", name):
            lines.append("  IXL: unavailable")
        elif ixl:
            totals = ixl.get("totals", {})
            for subj, t in totals.items():
                lines.append(f"  IXL {subj}: {t.get('done', '?')}/{t.get('assigned', '?')} done, "
                             f"{t.get('remaining', '?')} remaining")
        else:
            lines.append("  IXL: no data")

        sgy = state.get("schoology", {}).get(name, {})
        if scrape.unavailable_for(state, "schoology", name):
            lines.append("  SGY: unavailable")
        elif sgy:
            assignments = sgy.get("assignments", [])
            grades = sgy.get("grades", [])
            lines.append(f"  SGY: {len(assignments)} assignments, {len(grades)} courses")
            for g in grades:
                if g.get("grade"):
                    flag = ""
                    letter = g.get("letter", "")
                    if letter and letter[0] not in ("A", "B"):
                        flag = " !!!"
                    lines.append(f"    {g['course']}: {g['grade']}{flag}")
        else:
            lines.append("  SGY: no data")

        ps = state.get("powerschool", {}).get(name)
        if ps:
            lines.append("  PS: " + powerschool.format_counts(ps))

        lines.append("")

    pending = pending_action_items(state)
    if pending:
        lines.append(f"Action items ({len(pending)} pending):")
        for item in pending[:20]:
            due = f" (due {item['due'][:10]})" if item.get("due") else ""
            lines.append(f"  [{item['id']}] {item['child']}: {item['summary']}{due}")
    else:
        lines.append("No pending action items.")

    return "\n".join(lines)
