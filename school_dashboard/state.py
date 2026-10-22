import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from school_dashboard import maintenance, paths

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


def save(state: dict, path: Optional[str] = None) -> Path:
    p = _state_path(path)
    if maintenance.is_active():
        logger.warning("Maintenance mode active; not saving state to %s", p)
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now().isoformat()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)
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


def update_from_ixl_files(state: dict, ixl_dir: str = "/tmp/ixl") -> int:
    """Read all {name}-assigned.json files from ixl output dir and merge into state."""
    ixl_path = Path(ixl_dir)
    if not ixl_path.is_dir():
        return 0

    count = 0
    for f in ixl_path.glob("*-assigned.json"):
        child_name = _canonicalize(f.stem.replace("-assigned", ""))
        try:
            data = json.loads(f.read_text())
            update_ixl(state, child_name, data)
            count += 1
        except (json.JSONDecodeError, OSError):
            continue
    return count


def update_from_sgy_file(state: dict, sgy_file: str = "/tmp/schoology-daily.json") -> int:
    """Read sgy summary --json output and merge into state."""
    p = Path(sgy_file)
    if not p.exists():
        return 0

    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return 0

    count = 0
    for entry in data.get("per_child", []):
        child_info = entry.get("child", {})
        raw_name = child_info.get("name", "").split()[0]
        if not raw_name:
            continue
        name = _canonicalize(raw_name)
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
        if ixl:
            totals = ixl.get("totals", {})
            for subj, t in totals.items():
                lines.append(f"  IXL {subj}: {t['done']}/{t['assigned']} done, {t['remaining']} remaining")
        else:
            lines.append("  IXL: no data")

        sgy = state.get("schoology", {}).get(name, {})
        if sgy:
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
