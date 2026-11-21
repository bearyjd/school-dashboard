"""Fixture loader and builders for the recon v2 tests (all data is SYNTHETIC).

Imports of `school_dashboard.assignments` happen inside functions so this module itself always loads.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "recon"
TZ = ZoneInfo("America/New_York")


def d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def dt(value: str | None) -> datetime | None:
    """ISO datetime WITH an offset (fixtures always carry one)."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"fixture datetime needs an offset: {value}"
    return parsed


def local(y, mo, da, h=0, mi=0) -> datetime:
    """tz-aware America/New_York wall-clock datetime."""
    return datetime(y, mo, da, h, mi, tzinfo=TZ)


STATE_DEFAULTS: dict[str, Any] = dict(
    id=1, kid="Alex", class_name="Math 6", class_key="math 6", title="Worksheet", title_key="worksheet",
    due_date=None, kind="assignment", stage=1, stage_since="2026-11-16T12:00:00-05:00",
    received_evidence=None, portal_status="none", portal_conflict=False, closed_at=None, paper_only=False,
)
LINK_DEFAULTS: dict[str, Any] = dict(source="schoology", external_key="k1", norm_status="open", score=None, url=None,
                     live=True, trusted_misses=0, absent_since=None, last_miss_hash=None, via="scrape")


def mk_state(**over):
    from school_dashboard.assignments.model import AssignmentState
    f = {**STATE_DEFAULTS, **over}
    f["due_date"] = d(f["due_date"]) if isinstance(f["due_date"], str) else f["due_date"]
    for k in ("stage_since", "closed_at"):
        if isinstance(f[k], str):
            f[k] = dt(f[k])
    return AssignmentState(**f)


def mk_link(**over):
    from school_dashboard.assignments.model import LinkState
    f = {**LINK_DEFAULTS, **over}
    if isinstance(f["absent_since"], str):
        f["absent_since"] = d(f["absent_since"])
    return LinkState(**f)


def mk_record(**over):
    from school_dashboard.assignments.model import SourceRecord
    f: dict[str, Any] = dict(source="schoology", kid="Alex", class_name="Math 6", title="Worksheet", due_date=None,
             portal_status="open", raw_status="", score=None, link=None, external_key="x1", raw={}, via="scrape")
    f.update(over)
    if isinstance(f["due_date"], str):
        f["due_date"] = d(f["due_date"])
    return SourceRecord(**f)


# ---- scenario loading ------------------------------------------------------

def _scenarios(kind: str) -> list[dict]:
    out: list[dict] = []
    for p in sorted((FIXTURES / kind).glob("*.json")):
        data = json.loads(p.read_text())
        assert data.get("synthetic") is True, f"{p} must be marked synthetic"
        if "scenarios" in data:
            out.extend({**s, "_file": p.stem} for s in data["scenarios"])
        else:
            out.append({**data, "_file": p.stem})
    return out


def scenario_params(kind: str, needs_key: str | None = None, expect_key: str | None = None):
    """pytest params for every scenario; optionally only those whose `expect` has `expect_key`."""
    params = []
    for s in _scenarios(kind):
        if expect_key is not None and expect_key not in s.get("expect", {}):
            continue
        params.append(pytest.param(s, id=f"{s['_file']}::{s['id']}"))
    return params


def flag_inputs(s: dict) -> dict:
    """Keyword arguments for compute_flag, from a flags scenario."""
    return dict(
        now=dt(s["now"]),
        no_school=frozenset(d(x) for x in s.get("no_school", [])),
        grace_days=s.get("grace_days", 2),
        stale_sources=frozenset(s.get("stale_sources", [])),
        ingest_ok=s.get("ingest_ok", True),
    )


def compute_scenario_flag(s: dict):
    from school_dashboard.assignments.flags import compute_flag
    return compute_flag(mk_state(**s["state"]), [mk_link(**l) for l in s["links"]], **flag_inputs(s))


# ---- sequence runner (composes the pure modules like the S3 ingest will) ---

def _link_key(link) -> str:
    return f"{link.source}:{link.external_key}"


def _flag_now(state, links, at, cfg):
    from school_dashboard.assignments.flags import compute_flag
    return compute_flag(state, links, now=at, no_school=cfg["no_school"], grace_days=cfg["grace_days"])


def _apply_scrape(state, links, step, at):
    from school_dashboard.assignments import merge, schooldays, stages
    today = schooldays.school_today(at)
    new_links = []
    for ln in links:
        seen = _link_key(ln) in step["present"]
        info = step["present"].get(_link_key(ln), {})
        new_links.append(stages.register_scrape(
            ln, seen=seen, trusted=step["trusted"], file_hash=step["file_hash"], today=today,
            new_status=info.get("status"), new_score=info.get("score")))
    status, conflict = merge.combine_portal_status(new_links)
    state = replace(state, portal_status=status, portal_conflict=conflict)
    stage, evidence = stages.portal_decision(state, new_links, today)
    changed = stage != state.stage
    state = replace(state, stage=stage, received_evidence=evidence, stage_since=at if changed else state.stage_since)
    return state, new_links


def _apply_tap(state, step, at):
    from school_dashboard.assignments import stages
    try:
        return stages.apply_tap(state, step["action"], step["setter"], at), None
    except stages.StageError:
        return state, "StageError"


def run_sequence(seq: dict) -> list[tuple[dict, dict]]:
    """Returns [(step expectation, observed snapshot)] for every step."""
    cfg = {"no_school": frozenset(d(x) for x in seq.get("no_school", [])), "grace_days": seq.get("grace_days", 2)}
    state = mk_state(**seq["state"])
    links = [mk_link(**l) for l in seq["links"]]
    prev = _flag_now(state, links, dt(seq["start"]), cfg).flag
    out = []
    for step in seq["steps"]:
        at, error = dt(step["at"]), None
        if step["op"] == "scrape":
            state, links = _apply_scrape(state, links, step, at)
        elif step["op"] == "tap":
            state, error = _apply_tap(state, step, at)
        res = _flag_now(state, links, at, cfg)
        snap = {
            "stage": state.stage, "received_evidence": state.received_evidence, "portal_status": state.portal_status,
            "portal_conflict": state.portal_conflict, "flag": res.flag, "bucket": res.bucket,
            "info": sorted(res.info), "error": error,
            "transition": [prev, res.flag] if res.flag != prev else None,
            "links": {_link_key(l): {"live": l.live, "trusted_misses": l.trusted_misses, "norm_status": l.norm_status}
                      for l in links},
        }
        prev = res.flag
        out.append((step, snap))
    return out
