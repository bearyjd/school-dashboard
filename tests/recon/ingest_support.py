"""Harness for the S3b ingest tests: recon DB builder, adapter results from compact payloads, snapshots.

All data is SYNTHETIC. Imports of the package under test happen inside functions so this module loads
(and the RED tests fail per test, not at collection).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from recon.adapter_support import hkey, resolve
from recon.support import dt, local, mk_link, mk_state

SEED_NOW = local(2026, 11, 1, 12)
WED = local(2026, 11, 18, 17)


def make_db(tmp_path: Path, setup: Mapping[str, Any] | None = None, *, migrated: bool = True,
            baseline: bool = True) -> str:
    from school_dashboard import db
    from school_dashboard.assignments import repo, schema
    setup = setup or {}
    path = str(tmp_path / "school.db")
    db.init_db(path)
    conn = schema.connect(path)
    schema.ensure_schema(conn)
    with repo.transaction(conn):
        if migrated:
            repo.set_meta(conn, "migrated_at", "2026-11-01T00:00:00-05:00")
        if baseline:
            repo.set_meta(conn, "baseline_at", "2026-11-01T00:00:00-05:00")
        for cs in setup.get("class_settings", []):
            conn.execute(
                "INSERT INTO class_settings (kid, class_key, grace_school_days, paper_only, aliases)"
                " VALUES (?, ?, ?, ?, ?)",
                (cs["kid"], cs["class_key"], cs.get("grace_school_days"), cs.get("paper_only", 0),
                 json.dumps(cs["aliases"]) if "aliases" in cs else None))
        for seed in setup.get("seed", []):
            seed_assignment(conn, seed)
    conn.close()
    return path


def seed_assignment(conn, seed: Mapping[str, Any]) -> int:
    from school_dashboard.assignments import repo
    fields = {k: v for k, v in seed.items() if k != "links"}
    aid = repo.insert_assignment(conn, mk_state(id=None, **fields), now=SEED_NOW)
    for raw in seed.get("links", []):
        spec = dict(raw)
        key = spec.pop("external_key")
        if isinstance(key, dict):
            key = hkey(*key["hkey"])
        repo.upsert_link(conn, aid, seed["kid"], mk_link(external_key=key, **spec), now=SEED_NOW,
                         raw_status="", raw_json="{}")
    return aid


# ---- payload builders -----------------------------------------------------

def sgy_payload(spec: Mapping[str, list], top: Mapping[str, Any] | None = None) -> dict:
    per_child = []
    for kid, rows in spec.items():
        per_child.append({
            "child": {"name": kid}, "scrape_confidence": "high",
            "assignments": [{"title": r["title"], "course": r.get("course", ""), "due_date": r.get("due", ""),
                             "status": r.get("status", "unknown"), "link": r.get("link", "")} for r in rows]})
    return {"per_child": per_child, **(top or {})}


def ps_payload(spec: Mapping[str, Mapping[str, list]], top: Mapping[str, Any] | None = None) -> dict:
    def item(r):
        return {"course": r.get("course", ""), "title": r["title"], "due": r.get("due", ""),
                "category": None, "score": r.get("score")}
    children = [{"name": kid, "missing": [item(r) for r in lists.get("missing", [])],
                 "late": [item(r) for r in lists.get("late", [])],
                 "upcoming": [item(r) for r in lists.get("upcoming", [])], "warnings": []}
                for kid, lists in spec.items()]
    return {"scraped_at": "2026-11-18T10:00:00", "children": children, **(top or {})}


def ixl_payload(spec: Mapping[str, Mapping[str, list]]) -> dict:
    return {kid: {"status": "ok", "warnings": [], "errors": [], "remaining": [],
                  "totals": {subj: {"assigned": c[0], "done": c[1], "remaining": c[2]}
                             for subj, c in subjects.items()}}
            for kid, subjects in spec.items()}


def build_results(run: Mapping[str, Any]):
    """(results, file_hashes) from a run spec holding sgy / ps / ixl compact payloads."""
    from school_dashboard.assignments.adapters import ADAPTERS
    from school_dashboard.assignments.adapters.powerschool import PowerSchoolAdapter
    results = []
    if "sgy" in run:
        results.append(ADAPTERS["schoology"].parse(sgy_payload(run["sgy"], run.get("sgy_top")),
                                                   resolve_kid=resolve))
    if "ps" in run:
        results.append(PowerSchoolAdapter().parse(ps_payload(run["ps"], run.get("ps_top")),
                                                     resolve_kid=resolve, file_ok=run.get("ps_file_ok", True)))
    if "ixl" in run:
        results.append(ADAPTERS["ixl"].parse(ixl_payload(run["ixl"]), resolve_kid=resolve))
    return results, dict(run.get("hash", {}))


# ---- observation ----------------------------------------------------------

def snapshot(db_path: str) -> dict[str, Any]:
    from school_dashboard.assignments import repo, schema
    conn = schema.connect(db_path)
    try:
        out: dict[str, Any] = {"assignments": {}}
        for a in repo.list_assignments(conn, include_closed=True):
            row = conn.execute("SELECT flag FROM assignments WHERE id = ?", (a.id,)).fetchone()
            assert a.id is not None
            links = repo.links_for(conn, a.id)
            out["assignments"][f"{a.kid}|{a.title}|{a.class_name or ''}"] = {
                "id": a.id, "stage": a.stage, "portal_status": a.portal_status,
                "portal_conflict": a.portal_conflict, "closed": a.closed_at is not None,
                "evidence": a.received_evidence, "kind": a.kind, "flag": row["flag"],
                "link_count": len(links),
                "links": {ln.source: {"live": ln.live, "misses": ln.trusted_misses, "status": ln.norm_status,
                                      "absent_since": ln.absent_since.isoformat() if ln.absent_since else None}
                          for ln in links}}
        kinds: dict[str, int] = {}
        for r in conn.execute("SELECT kind, detail FROM assignment_events"):
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
            if r["kind"] == "merge" and (r["detail"] or "").startswith("possible_duplicate"):
                kinds["possible_duplicate"] = kinds.get("possible_duplicate", 0) + 1
        out["event_kinds"] = kinds
        out["events"] = sum(v for k, v in kinds.items() if k != "possible_duplicate")
        out["outbox"] = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM push_outbox GROUP BY status")}
        out["outbox_total"] = sum(out["outbox"].values())
        out["baseline_at"] = repo.get_meta(conn, "baseline_at")
        return out
    finally:
        conn.close()


def check_expect(expect: Mapping[str, Any], before: Mapping[str, Any], after: Mapping[str, Any], report,
                 where: str) -> None:
    for key, want in expect.items():
        if key == "_count":
            assert len(after["assignments"]) == want, where
        elif key == "_events_delta":
            assert after["events"] - before["events"] == want, where
        elif key == "_outbox_delta":
            assert after["outbox_total"] - before["outbox_total"] == want, where
        elif key == "_outbox":
            for status, n in want.items():
                assert after["outbox"].get(status, 0) == n, f"{where}: outbox {status}"
        elif key == "_kinds_delta":
            for kind, n in want.items():
                assert after["event_kinds"].get(kind, 0) - before["event_kinds"].get(kind, 0) == n, f"{where}: {kind}"
        elif key == "_possible_duplicate":
            assert after["event_kinds"].get("possible_duplicate", 0) == want, where
        elif key == "_ok":
            assert report.ok is want, where
        elif key == "_baseline":
            assert report.baseline is want, where
        elif key == "_baseline_at_set":
            assert (after["baseline_at"] is not None) is want, where
        elif key == "_warnings":
            joined = " | ".join(report.warnings)
            for part in want:
                assert part in joined, f"{where}: warning {part!r} not in {joined!r}"
        else:
            assert key in after["assignments"], f"{where}: no assignment {key!r} in {sorted(after['assignments'])}"
            _check_assignment(after["assignments"][key], want, f"{where} {key}")


def _check_assignment(got: Mapping[str, Any], want: Mapping[str, Any], where: str) -> None:
    for field, value in want.items():
        if field == "links":
            for source, link_want in value.items():
                assert source in got["links"], f"{where}: no {source} link"
                for k, v in link_want.items():
                    assert got["links"][source][k] == v, f"{where}: link {source}.{k}"
        else:
            assert got[field] == value, f"{where}: {field} = {got[field]!r}, want {value!r}"


def drive(tmp_path: Path, sc: Mapping[str, Any]) -> None:
    """Run every step of an ingest scenario through the real adapters, ingest and SQLite."""
    from school_dashboard.assignments import ingest
    setup = sc.get("setup", {})
    db_path = make_db(tmp_path, setup, baseline=setup.get("baseline_done", True))
    rows = setup.get("no_school_rows", [])
    for i, run in enumerate(sc["runs"]):
        before = snapshot(db_path)
        results, hashes = build_results(run)
        now = dt(run["now"])
        assert now is not None
        report = ingest.run(db_path, results, file_hashes=hashes, now=now, no_school_rows=rows)
        check_expect(run["expect"], before, snapshot(db_path), report, f"{sc['_file']}::{sc['id']} run{i + 1}")


def ns(**kw: Any):
    from types import SimpleNamespace
    return SimpleNamespace(**kw)


def iso(value: datetime) -> str:
    return value.isoformat()
