"""The `school-state update` hook must be failure-proof; push policy gap; stderr equivalence."""
import argparse
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from contract_support import build_state, load, set_roster
from recon.ingest_support import WED, make_db
from recon.report_support import REPO
from recon.test_shadow_hook import _update_in

ROSTER = {"children": {n: {"grade": "5th", "school": "Sample School"} for n in ("Alpha", "Beta", "Gamma")}}


@pytest.fixture(autouse=True)
def _clean_flags(monkeypatch):
    for name in ("RECON_INGEST_ENABLED", "RECON_PUSH_ENABLED"):
        monkeypatch.delenv(name, raising=False)


# ---- (a) hook is failure-proof -------------------------------------------------

def _inputs(root: Path) -> Path:
    inputs = root / "inputs"
    (inputs / "ixl").mkdir(parents=True)
    (inputs / "ixl" / "alpha-assigned.json").write_text(json.dumps(load("ixl")))
    (inputs / "sgy.json").write_text(json.dumps(load("sgy")))
    (inputs / "ps.json").write_text(json.dumps(load("powerschool")))
    return inputs


def _run_update(root: Path, *, flag: bool, probe: str = "") -> subprocess.CompletedProcess:
    state_dir = root / "state"
    state_dir.mkdir(parents=True)
    (root / "roster.json").write_text(json.dumps(ROSTER))
    inputs = _inputs(root)
    if flag:
        make_db(state_dir)
    env = {**os.environ, "SCHOOL_STATE_DIR": str(state_dir), "PYTHONPATH": str(REPO),
           "SCHOOL_DASHBOARD_CONFIG": str(root / "roster.json")}
    for k in ("SCHOOL_STATE_PATH", "SCHOOL_DB_PATH", "RECON_INGEST_ENABLED", "RECON_PUSH_ENABLED"):
        env.pop(k, None)
    if flag:
        env["RECON_INGEST_ENABLED"] = "1"
    code = ("import sys\nfrom school_dashboard import cli\n"
            f"sys.argv = ['school-state', 'update', '--ixl-dir', {str(inputs / 'ixl')!r}, "
            f"'--sgy-file', {str(inputs / 'sgy.json')!r}, '--ps-file', {str(inputs / 'ps.json')!r}]\n"
            "cli.main()\n" + probe)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=REPO)


def test_flag_off_update_does_not_import_the_recon_engine(tmp_path):
    probe = ("print('ingest' if 'school_dashboard.assignments.ingest' in sys.modules else 'clean',\n"
             "      'notify' if 'school_dashboard.assignments.notify' in sys.modules else 'clean')\n")
    r = _run_update(tmp_path, flag=False, probe=probe)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["clean", "clean"]


def test_flag_on_with_unimportable_recon_code_still_completes_and_records_error(tmp_path, monkeypatch, caplog):
    off_bytes, _, _ = _update_in(tmp_path / "off", monkeypatch, flag=False)
    monkeypatch.setitem(sys.modules, "school_dashboard.assignments.ingest", None)  # import raises ImportError
    on_bytes, meta, state_dir = _update_in(tmp_path / "on", monkeypatch, flag=True)
    assert on_bytes == off_bytes
    assert (state_dir / "school-state.json").exists()
    assert meta["recon_ingest"]["last_result"] == "error"
    assert "ImportError" in caplog.text or "ModuleNotFoundError" in caplog.text


def test_run_shadow_raising_before_its_own_guard_is_contained(tmp_path, monkeypatch, caplog):
    from school_dashboard.assignments import ingest
    off_bytes, _, _ = _update_in(tmp_path / "off", monkeypatch, flag=False)

    def explode(*a, **k):
        raise RuntimeError("secret detail Worksheet")

    monkeypatch.setattr(ingest, "run_shadow", explode)
    on_bytes, meta, _ = _update_in(tmp_path / "on", monkeypatch, flag=True)
    assert on_bytes == off_bytes and meta["recon_ingest"]["last_result"] == "error"
    assert "RuntimeError" in caplog.text and "secret detail" not in caplog.text


def test_hook_survives_the_error_recording_failing_too(state_env, monkeypatch):
    from school_dashboard import cli, sync_meta
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    monkeypatch.setitem(sys.modules, "school_dashboard.assignments.ingest", None)

    def no_write(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(sync_meta, "write_sync_source", no_write)
    cli._recon_shadow(argparse.Namespace(sgy_file="x", ps_file="y", ixl_dir="z", state_file=None))  # must not raise


def test_hook_flag_off_returns_without_touching_anything(state_env, monkeypatch):
    from school_dashboard import cli
    monkeypatch.setitem(sys.modules, "school_dashboard.assignments.ingest", None)
    cli._recon_shadow(argparse.Namespace())
    assert not (state_env / "sync_meta.json").exists()


# ---- (b) push policy: portal evidence arriving on an already date-missing record -----

def _ps_run(path, status_list, now, h):
    from recon.ingest_support import build_results
    from school_dashboard.assignments import ingest
    row = {"title": "Worksheet", "course": "Math 6", "due": "2026-11-16"}
    results, hashes = build_results({"ps": {"Alex": {status_list: [row]}}, "hash": {"powerschool": h}})
    return ingest.run(path, results, file_hashes=hashes, now=now, no_school_rows=[])


def _outbox(path):
    from school_dashboard.assignments import schema
    conn = schema.connect(path)
    try:
        return [(r["flag"], r["occurrence_at"], r["status"], r["reason"]) for r in conn.execute(
            "SELECT * FROM push_outbox ORDER BY id")]
    finally:
        conn.close()


def _seeded(tmp_path, baseline):
    seed = [{"kid": "Alex", "title": "Worksheet", "title_key": "worksheet", "class_name": "Math 6",
             "class_key": "math 6", "due_date": "2026-11-16"}]
    return make_db(tmp_path, {"seed": seed}, baseline=baseline)


def test_portal_missing_arriving_on_a_date_missing_record_enqueues_one_push(tmp_path):
    path = _seeded(tmp_path, baseline=True)
    _ps_run(path, "upcoming", WED, "h1")
    assert _outbox(path) == []                      # date-only overdue never enqueues
    later = WED + timedelta(days=1)
    _ps_run(path, "missing", later, "h2")
    assert _outbox(path) == [("missing", later.isoformat(), "pending", "transition")]
    _ps_run(path, "missing", later + timedelta(days=1), "h3")
    _ps_run(path, "missing", later + timedelta(days=2), "h4")
    assert len(_outbox(path)) == 1                  # merely staying portal-missing enqueues nothing


def test_that_push_is_suppressed_during_the_baseline_run(tmp_path):
    from school_dashboard.assignments import schema
    path = _seeded(tmp_path, baseline=False)
    conn = schema.connect(path)
    conn.execute("UPDATE assignments SET flag = 'missing', flag_since = ?", (WED.isoformat(),))
    conn.close()                                    # already date-missing, and this is the baseline run
    _ps_run(path, "missing", WED + timedelta(days=1), "h2")
    rows = _outbox(path)
    assert len(rows) == 1 and rows[0][2:] == ("suppressed", "baseline")


# ---- (c) stderr is identical with the flag off and on (ingest succeeding) ---------------

def _norm(stderr: str, root: Path) -> list[str]:
    return stderr.replace(str(root), "<ROOT>").splitlines()


def test_stderr_is_line_for_line_identical_between_flag_off_and_on(tmp_path):
    off = _run_update(tmp_path / "off", flag=False)
    on = _run_update(tmp_path / "on", flag=True)
    assert off.returncode == on.returncode == 0
    assert off.stdout == on.stdout
    assert _norm(off.stderr, tmp_path / "off") == _norm(on.stderr, tmp_path / "on")
    meta = json.loads((tmp_path / "on" / "state" / "sync_meta.json").read_text())
    assert meta["recon_ingest"]["last_result"] == "ok"   # the ingest really ran
    assert (tmp_path / "off" / "state" / "school-state.json").read_bytes()  # sanity: state written
