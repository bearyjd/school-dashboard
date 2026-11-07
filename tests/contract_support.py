"""Shared helpers for the scraper contract tests (tests/test_contract_*.py).

Fixtures live in tests/fixtures/contracts/ (see the README there). Every date in them
is relative to ANCHOR; `load` shifts all of them so "today" is the real today and
the freshness / due-date logic in the consumers behaves as it does in production.
"""
import json
import re
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from unittest.mock import patch

from school_dashboard import cli, digest, readiness, state
from school_dashboard.overview_items import build_items

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
ANCHOR = date(2026, 11, 5)
ROSTER = ("Alpha", "Beta", "Gamma")

_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})(T.*)?$")
_FREEFORM = re.compile(r"^([A-Za-z]+ \d{1,2}, \d{4})$")


def _shift_str(value: str, days: int) -> str:
    iso = _ISO.match(value)
    if iso:
        moved = date.fromisoformat(iso.group(1)) + timedelta(days=days)
        return moved.isoformat() + (iso.group(2) or "")
    free = _FREEFORM.match(value)
    if free:
        try:
            moved = datetime.strptime(value, "%B %d, %Y").date() + timedelta(days=days)
        except ValueError:
            return value
        return f"{moved:%B} {moved.day}, {moved.year}"
    return value


def shift_dates(obj: object, days: int) -> object:
    """New structure with every ISO / 'Month D, YYYY' string moved by `days`."""
    if isinstance(obj, dict):
        return {k: shift_dates(v, days) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shift_dates(v, days) for v in obj]
    if isinstance(obj, str):
        return _shift_str(obj, days)
    return obj


def load(name: str, today: Optional[date] = None) -> dict:
    """Fixture `name` ('ixl', 'sgy', 'gc', 'powerschool') rebased so ANCHOR becomes `today`."""
    today = today or date.today()
    raw = json.loads((FIXTURES / f"{name}.json").read_text())
    return shift_dates(raw, (today - ANCHOR).days)


def without(obj: dict, key: str) -> dict:
    """Copy of dict `obj` minus `key`."""
    return {k: v for k, v in obj.items() if k != key}


def renamed(obj: dict, old: str, new: str) -> dict:
    """Copy of dict `obj` with key `old` renamed to `new`, order preserved."""
    return {(new if k == old else k): v for k, v in obj.items()}


def set_roster(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "roster.json"
    cfg.write_text(json.dumps({"children": {
        name: {"grade": "5th", "school": "Sample School"} for name in ROSTER}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    state._config_cache = None


def build_state(state_env: Path, tmp_path: Path, *, ixl: Optional[dict] = None,
                sgy: Optional[dict] = None, ps: Optional[dict] = None) -> dict:
    """Run `school-state update` over scraper payloads; returns {'state', 'path'}.

    A payload of None means the fixture unchanged; pass a dict to override it.
    The IXL payload is written for Alpha only (`alpha-assigned.json`).
    """
    inputs = tmp_path / "inputs"
    (inputs / "ixl").mkdir(parents=True, exist_ok=True)
    (inputs / "ixl" / "alpha-assigned.json").write_text(json.dumps(ixl or load("ixl")))
    (inputs / "sgy.json").write_text(json.dumps(sgy or load("sgy")))
    ps_data = ps or load("powerschool")
    if ps is None:
        ps_data["scraped_at"] = datetime.now().isoformat(timespec="seconds")
    (inputs / "ps.json").write_text(json.dumps(ps_data))
    argv = ["school-state", "update", "--ixl-dir", str(inputs / "ixl"),
            "--sgy-file", str(inputs / "sgy.json"), "--ps-file", str(inputs / "ps.json")]
    with patch.object(sys, "argv", argv):
        cli.main()
    path = state_env / "school-state.json"
    return {"state": json.loads(path.read_text()), "path": path}


def consumers(built: dict, today: Optional[date] = None) -> dict:
    """Run the read-side consumers over a built state: quick check, overview items, readiness."""
    today = today or date.today()
    text, _ = digest.build_quick_check(str(built["path"]), today=today.isoformat())
    return {
        "quick": text,
        "items": build_items(built["state"], [], today, list(ROSTER)),
        "readiness": readiness.get_checklist(str(built["path"])),
    }


def mutate(data: dict, fn: Callable[[dict], None]) -> dict:
    """Deep copy of `data` after `fn` edited it in place (fixtures are never touched)."""
    copy = deepcopy(data)
    fn(copy)
    return copy
