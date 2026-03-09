import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from markupsafe import Markup
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _letter_class(letter: str) -> str:
    if not letter:
        return ""
    first = letter[0].upper()
    if first in ("A", "B"):
        return "grade-ok"
    if first == "C":
        return "grade-warn"
    return "grade-bad"


def _format_due(due_str: str) -> str:
    if not due_str:
        return ""
    try:
        dt = datetime.fromisoformat(due_str[:19])
        return dt.strftime("%a %b %-d")
    except (ValueError, TypeError):
        return due_str[:20]


def _is_overdue(due_str: str) -> bool:
    if not due_str:
        return False
    try:
        dt = datetime.fromisoformat(due_str[:10])
        return dt.date() < datetime.now().date()
    except (ValueError, TypeError):
        return False


def _is_due_tomorrow(due_str: str) -> bool:
    if not due_str:
        return False
    try:
        from datetime import timedelta
        dt = datetime.fromisoformat(due_str[:10])
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        return dt.date() == tomorrow
    except (ValueError, TypeError):
        return False


def render(state: dict, output_path: Optional[str] = None) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["letter_class"] = _letter_class
    env.filters["format_due"] = _format_due
    env.filters["is_overdue"] = _is_overdue
    env.filters["is_due_tomorrow"] = _is_due_tomorrow

    template = env.get_template("dashboard.html")

    children_data = []
    for name, info in state.get("children", {}).items():
        ixl = state.get("ixl", {}).get(name, {})
        sgy = state.get("schoology", {}).get(name, {})
        children_data.append({
            "name": name,
            "grade": info.get("grade", ""),
            "ixl_totals": ixl.get("totals", {}),
            "ixl_updated": ixl.get("updated", ""),
            "assignments": sgy.get("assignments", []),
            "grades": sgy.get("grades", []),
            "sgy_updated": sgy.get("updated", ""),
        })

    pending = [i for i in state.get("action_items", []) if i["status"] == "pending"]
    pending.sort(key=lambda x: x.get("due") or "9999")

    html = template.render(
        children=children_data,
        action_items=pending,
        last_updated=state.get("last_updated", "never"),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        state_json=Markup(json.dumps(state, indent=2)),
    )

    if output_path:
        out = Path(output_path)
    else:
        from school_dashboard.state import _state_path
        out = _state_path().parent / "school-dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out
