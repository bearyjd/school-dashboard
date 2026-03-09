import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_STATE_PATH = Path("/var/lib/openclaw/school-state.json")
DEFAULT_CONFIG_PATH = Path("/etc/school-dashboard/config.json")

PRUNE_COMPLETED_AFTER_DAYS = 7
PRUNE_PAST_DUE_AFTER_DAYS = 3

_config_cache: Optional[dict] = None


def _config_path() -> Path:
    env = os.environ.get("SCHOOL_DASHBOARD_CONFIG")
    if env:
        return Path(env)
    return DEFAULT_CONFIG_PATH


def _load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    p = _config_path()
    if p.exists():
        try:
            _config_cache = json.loads(p.read_text())
            return _config_cache
        except (json.JSONDecodeError, OSError):
            pass

    _config_cache = {"children": {}, "name_aliases": {}}
    return _config_cache


def get_children() -> dict:
    return _load_config().get("children", {})


def get_name_aliases() -> dict:
    return _load_config().get("name_aliases", {})


def _canonicalize(name: str) -> str:
    aliases = get_name_aliases()
    lower = name.strip().lower()
    return aliases.get(lower, name.strip().title())


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
    env = os.environ.get("SCHOOL_STATE_PATH")
    if env:
        return Path(env)
    return DEFAULT_STATE_PATH


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
