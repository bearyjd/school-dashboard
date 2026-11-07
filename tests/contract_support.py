"""Shared helpers for the scraper contract tests (tests/test_contract_*.py).

Fixtures live in tests/fixtures/contracts/ (see the README there). Every date in them
is relative to ANCHOR; `load` shifts all of them so "today" is the real today and
the freshness / due-date logic in the consumers behaves as it does in production.
"""
import json
import re
import sys
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, TypeVar, cast
from unittest.mock import patch

import pytest

from school_dashboard import cli, digest, readiness, schoology_autoarchive, state
from school_dashboard.overview_items import build_items

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
ANCHOR = date(2026, 11, 5)
TODAY = ANCHOR  # every test runs on this frozen day; nothing reads the wall clock
FROZEN_NOW = datetime(2026, 11, 5, 12, 0, 0)
ROSTER = ("Alpha", "Beta", "Gamma")
T = TypeVar("T", bound=dict)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return cls.combine(FROZEN_NOW.date(), FROZEN_NOW.time())


class _FrozenDate(date):
    @classmethod
    def today(cls):  # noqa: ANN206
        return cls(TODAY.year, TODAY.month, TODAY.day)


def assert_unguarded(condition: bool, gap_id: str) -> None:
    """Assert today's (bad) behaviour of an unguarded gap, failing with guidance."""
    assert condition, (
        f"UNGUARDED gap {gap_id} closed (or changed): flip this test to the guarded "
        "assertion - see tests/fixtures/contracts/README.md step 4")


@contextmanager
def unguarded_raises(exc: type[BaseException], gap_id: str) -> Iterator[None]:
    """Like pytest.raises for a gap that currently crashes; a clean run fails with guidance."""
    try:
        yield
    except exc:
        return
    pytest.fail(f"UNGUARDED gap {gap_id} closed (or changed): no {exc.__name__} raised; flip this "
                "test to the guarded assertion - see tests/fixtures/contracts/README.md step 4")

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


def shift_dates(obj: Any, days: int) -> Any:
    """New structure with every ISO / 'Month D, YYYY' string moved by `days`."""
    if isinstance(obj, dict):
        return {k: shift_dates(v, days) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shift_dates(v, days) for v in obj]
    if isinstance(obj, str):
        return _shift_str(obj, days)
    return obj


def load(name: str, today: date = TODAY) -> dict[str, Any]:
    """Fixture `name` ('ixl', 'sgy', 'gc', 'powerschool') rebased so ANCHOR becomes `today`."""
    raw = json.loads((FIXTURES / f"{name}.json").read_text())
    return cast("dict[str, Any]", shift_dates(raw, (today - ANCHOR).days))


def without(obj: dict[str, Any], key: str) -> dict[str, Any]:
    """Copy of dict `obj` minus `key`."""
    return {k: v for k, v in obj.items() if k != key}


def renamed(obj: dict[str, Any], old: str, new: str) -> dict[str, Any]:
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
    (inputs / "ps.json").write_text(json.dumps(ps or load("powerschool")))
    argv = ["school-state", "update", "--ixl-dir", str(inputs / "ixl"),
            "--sgy-file", str(inputs / "sgy.json"), "--ps-file", str(inputs / "ps.json")]
    with (patch.object(sys, "argv", argv),
          patch.object(state, "datetime", _FrozenDatetime),
          patch.object(schoology_autoarchive, "datetime", _FrozenDatetime)):
        cli.main()
    path = state_env / "school-state.json"
    return {"state": json.loads(path.read_text()), "path": path}


def consumers(built: dict, today: date = TODAY) -> dict:
    """Run the read-side consumers over a built state: quick check, overview items, readiness."""
    text, _ = digest.build_quick_check(str(built["path"]), today=today.isoformat())
    with patch.object(readiness, "date", _FrozenDate):
        checklist = readiness.get_checklist(str(built["path"]))
    return {
        "quick": text,
        "items": build_items(built["state"], [], today, list(ROSTER)),
        "readiness": checklist,
    }


def mutate(data: T, fn: Callable[[T], None]) -> T:
    """Deep copy of `data` after `fn` edited it in place (fixtures are never touched)."""
    copy = deepcopy(data)
    fn(copy)
    return copy
