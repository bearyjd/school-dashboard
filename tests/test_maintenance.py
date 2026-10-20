"""Maintenance flag lifecycle and enforcement points."""
import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from school_dashboard import maintenance

REPO = Path(__file__).resolve().parent.parent


# ── flag lifecycle ───────────────────────────────────────────────────────────

def test_inactive_by_default(state_env):
    assert maintenance.is_active() is False
    assert maintenance.status() is None


def test_enable_writes_flag_with_fields_and_mode(state_env):
    info = maintenance.enable("backup")
    assert maintenance.is_active() is True
    assert info["reason"] == "backup" and info["operation_id"] and info["since"]
    flag = state_env / "maintenance.json"
    assert json.loads(flag.read_text()) == info
    assert stat.S_IMODE(flag.stat().st_mode) == 0o600
    assert not list(state_env.glob("*.tmp*"))


def test_enable_is_idempotent_keeps_operation_id(state_env):
    first = maintenance.enable("a")
    second = maintenance.enable("b")
    assert second["operation_id"] == first["operation_id"]
    assert second["since"] == first["since"]


def test_disable_clears_and_is_safe_when_inactive(state_env):
    maintenance.enable("x")
    assert maintenance.disable() is True
    assert maintenance.is_active() is False
    assert maintenance.disable() is False


def test_corrupt_flag_fails_closed_with_warning(state_env, caplog):
    (state_env / "maintenance.json").write_text("{not json")
    with caplog.at_level("WARNING"):
        assert maintenance.is_active() is True
    assert "maintenance" in caplog.text.lower()
    assert maintenance.status()["corrupt"] is True


def test_non_object_flag_fails_closed(state_env):
    (state_env / "maintenance.json").write_text("[]")
    assert maintenance.is_active() is True


def test_enable_replaces_corrupt_flag_and_disable_clears_it(state_env):
    (state_env / "maintenance.json").write_text("garbage")
    info = maintenance.enable("fix")
    assert info["operation_id"]
    assert "corrupt" not in maintenance.status()
    (state_env / "maintenance.json").write_text("garbage")
    assert maintenance.disable() is True
    assert maintenance.is_active() is False


# ── Flask ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(state_env, monkeypatch):
    monkeypatch.setenv("SYNC_TOKEN", "tok")
    from web.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


WRITE_ROUTES = [
    ("post", "/api/items", {"child": "A", "title": "t"}),
    ("patch", "/api/items/1", {"title": "t"}),
    ("delete", "/api/items/1", None),
    ("patch", "/api/digest/abc/cards/0", {"done": True}),
    ("post", "/api/agent/inline", {"prompt": "hi"}),
    ("post", "/api/sync", {"sources": "ixl"}),
]


@pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
def test_flask_write_routes_503_when_active(client, method, path, body):
    maintenance.enable("test")
    kwargs = {"json": body} if body is not None else {}
    r = getattr(client, method)(path, headers={"X-Sync-Token": "tok"}, **kwargs)
    assert r.status_code == 503
    assert r.get_json() == {"error": "maintenance"}
    assert "test" not in r.get_data(as_text=True)
    assert r.headers.get("Retry-After")


def test_flask_get_unaffected_when_active(client):
    maintenance.enable("test")
    assert client.get("/api/items").status_code == 200
    assert client.get("/api/sync/status").status_code == 200


def test_flask_writes_work_when_inactive(client):
    r = client.post("/api/items", json={"child": "A", "title": "t"})
    assert r.status_code != 503


def test_flask_corrupt_flag_blocks_writes(client, state_env):
    (state_env / "maintenance.json").write_text("zzz")
    assert client.post("/api/items", json={"child": "A", "title": "t"}).status_code == 503


# ── send_ntfy / prune guards ─────────────────────────────────────────────────

def test_send_ntfy_noop_when_active(state_env):
    from school_dashboard import digest
    maintenance.enable("t")
    db = str(state_env / "school.db")
    with patch("school_dashboard.digest.requests.post") as post, \
         patch("school_dashboard.db.purge_old_digests") as purge, \
         patch("school_dashboard.db.create_digest") as create:
        digest.send_ntfy("topic", "msg", "Title", cards=[{"a": 1}], db_path=db)
    post.assert_not_called()
    purge.assert_not_called()
    create.assert_not_called()


def test_send_ntfy_posts_when_inactive(state_env):
    from school_dashboard import digest
    with patch("school_dashboard.digest.requests.post") as post:
        digest.send_ntfy("topic", "msg", "Title")
    post.assert_called_once()


def test_purge_old_digests_guarded(state_env):
    from school_dashboard import db
    path = str(state_env / "school.db")
    db.init_db(path)
    did = db.create_digest(path, "t", [{"x": 1}])
    import sqlite3
    with sqlite3.connect(path) as c:
        c.execute("UPDATE digests SET created_at='2000-01-01 00:00:00'")
    maintenance.enable("t")
    assert db.purge_old_digests(path, days=1) == 0
    assert db.get_digest(path, did) is not None


def test_prune_stale_guarded(state_env):
    from school_dashboard import state
    s = state.load(str(state_env / "s.json"))
    s["action_items"].append({
        "id": "1", "status": "completed", "completed_at": "2000-01-01T00:00:00",
        "child": "A", "summary": "x", "created": "2000-01-01T00:00:00",
    })
    maintenance.enable("t")
    assert state.prune_stale(s) == 0
    assert len(s["action_items"]) == 1


# ── CLI ──────────────────────────────────────────────────────────────────────

def _run_cli(*argv):
    from school_dashboard import cli
    with patch("sys.argv", ["school-state", *argv]):
        with pytest.raises(SystemExit) as ei:
            cli.main()
    return ei.value.code


@pytest.mark.parametrize("argv", [
    ("update",), ("html",), ("action", "add", "A", "do it"),
    ("action", "complete", "1"), ("email-sync", "--account", "a@b.c"),
    ("digest", "--mode", "morning"),
])
def test_cli_write_commands_exit_3_when_active(state_env, capsys, argv):
    maintenance.enable("t")
    assert _run_cli(*argv) == 3
    assert "maintenance" in capsys.readouterr().err.lower()


def test_cli_read_and_transition_unaffected(state_env, capsys):
    from school_dashboard import cli
    maintenance.enable("t")
    for argv in (("show",), ("action", "list"), ("email-show",),
                 ("transition", "maintenance", "status")):
        with patch("sys.argv", ["school-state", *argv]):
            cli.main()
    capsys.readouterr()


def test_cli_write_works_when_inactive(state_env):
    from school_dashboard import cli
    with patch("sys.argv", ["school-state", "--state-file",
                            str(state_env / "s.json"), "action", "add", "A", "x"]):
        cli.main()
    assert (state_env / "s.json").exists()


# ── shell scripts ────────────────────────────────────────────────────────────

SCRIPTS = [
    ("sync/school-sync.sh", []),
    ("sync/run-digest.sh", ["morning"]),
    ("sync/run-weekly.sh", ["friday"]),
    ("sync/gc-scrape.sh", []),
]


def _stub(tmp_path, exit_code, update_code=0):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    calls = tmp_path / "calls.log"
    s = d / "school-state"
    s.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n'
                 f'[ "$1 $2 $3" = "transition maintenance check" ] && exit {exit_code}\n'
                 f'[ "$1" = "update" ] && exit {update_code}\nexit 0\n')
    s.chmod(0o755)
    curl = d / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    return d, calls


def _run_script(script, args, path, state_dir):
    env = {
        "PATH": path, "HOME": str(state_dir),
        "SCHOOL_DASHBOARD_ENV": "/nonexistent",
        "SCHOOL_STATE_PATH": str(state_dir / "school-state.json"),
        "SCHOOL_GC_PATH": str(state_dir / "gc-schedule.json"),
        "NTFY_TOPIC": "x",
    }
    return subprocess.run(["bash", str(REPO / script), *args], env=env,
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("script,args", SCRIPTS)
def test_scripts_skip_when_active(tmp_path, script, args):
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, calls = _stub(tmp_path, 0)
    r = _run_script(script, args, f"{bindir}:/usr/bin:/bin", sd)
    assert r.returncode == 0, r.stderr
    log = sd / "digest.log"
    text = r.stderr + r.stdout + (log.read_text() if log.exists() else "")
    if script.endswith("run-weekly.sh"):
        text += (sd / "digest-weekly.log").read_text()
    assert "maintenance mode" in text.lower()
    assert calls.read_text().strip() == "transition maintenance check"


@pytest.mark.parametrize("script,args", SCRIPTS[1:3])
def test_scripts_continue_when_inactive(tmp_path, script, args):
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, calls = _stub(tmp_path, 10)
    r = _run_script(script, args, f"{bindir}:/usr/bin:/bin", sd)
    assert "maintenance mode" not in (r.stderr + r.stdout).lower()
    assert len(calls.read_text().strip().splitlines()) >= 1
    if script.endswith("run-digest.sh"):
        assert "digest --mode morning" in calls.read_text()




def _skip_text(r, sd):
    text = r.stderr + r.stdout
    for log in sd.glob("*.log"):
        text += log.read_text()
    return text.lower()


@pytest.mark.parametrize("script,args", SCRIPTS)
@pytest.mark.parametrize("code", [1, 2, 3, 127])
def test_scripts_fail_closed_on_check_crash(tmp_path, script, args, code):
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, calls = _stub(tmp_path, code)
    r = _run_script(script, args, f"{bindir}:/usr/bin:/bin", sd)
    assert r.returncode == 0, r.stderr
    assert "maintenance" in _skip_text(r, sd)
    assert calls.read_text().strip() == "transition maintenance check"


@pytest.mark.parametrize("script,args", SCRIPTS)
def test_scripts_skip_when_school_state_missing(tmp_path, script, args):
    if any(os.access(Path(p) / "school-state", os.X_OK) for p in ("/usr/bin", "/bin")):
        pytest.skip("school-state present in system PATH")
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, _ = _stub(tmp_path, 10)
    (bindir / "school-state").unlink()
    r = _run_script(script, args, f"{bindir}:/usr/bin:/bin", sd)
    assert r.returncode == 0, r.stderr
    assert "maintenance" in _skip_text(r, sd)


def _counting_stub(tmp_path, inactive_calls, update_code=0):
    """check exits 1 for the first N calls, then 0 (maintenance turns on)."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    calls, counter = tmp_path / "calls.log", tmp_path / "n"
    counter.write_text("0")
    s = d / "school-state"
    s.write_text(f"""#!/bin/sh
echo "$@" >> {calls}
if [ "$1 $2 $3" = "transition maintenance check" ]; then
  ls "$SCHOOL_STATE_DIR"/sync.running.* >/dev/null 2>&1 && echo check-saw-marker >> {calls}
  n=$(cat {counter}); n=$((n+1)); echo $n > {counter}
  [ "$n" -le {inactive_calls} ] && exit 10
  exit 0
fi
ls "$SCHOOL_STATE_DIR"/sync.running.* >/dev/null 2>&1 && echo marker-present >> {calls}
[ "$1" = "update" ] && exit {update_code}
exit 0
""")
    s.chmod(0o755)
    return d, calls


def _sync_env_run(tmp_path, bindir, sd):
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(sd),
           "SCHOOL_DASHBOARD_ENV": "/nonexistent", "SCHOOL_STATE_DIR": str(sd),
           "SCHOOL_STATE_PATH": str(sd / "school-state.json"),
           "IXL_DIR": str(tmp_path / "ixl"), "SGY_FILE": str(tmp_path / "sgy.json")}
    return subprocess.run(["bash", str(REPO / "sync/school-sync.sh")], env=env,
                          capture_output=True, text=True, timeout=30)


def test_school_sync_rechecks_before_state_update(tmp_path):
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, calls = _counting_stub(tmp_path, inactive_calls=1)
    r = _sync_env_run(tmp_path, bindir, sd)
    assert r.returncode == 0, r.stderr
    assert "update" not in calls.read_text().split()
    assert "maintenance" in r.stderr.lower()
    assert not list(sd.glob("sync.running*"))


def test_school_sync_maps_exit_3_to_skip(tmp_path):
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, calls = _counting_stub(tmp_path, inactive_calls=99, update_code=3)
    r = _sync_env_run(tmp_path, bindir, sd)
    assert r.returncode == 0, r.stderr
    assert "state update failed" not in r.stderr.lower()
    assert "marker-present" in calls.read_text()
    assert not list(sd.glob("sync.running*"))


def test_school_sync_real_update_failure_still_fails(tmp_path):
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, _ = _counting_stub(tmp_path, inactive_calls=99, update_code=1)
    r = _sync_env_run(tmp_path, bindir, sd)
    assert r.returncode == 1
    assert not list(sd.glob("sync.running*"))


# ── chat exempt, enable race, sync marker, extra writers ─────────────────────

def test_chat_not_gated_when_active(client):
    maintenance.enable("t")
    with patch("web.app.chat_completion", return_value="hi"):
        r = client.post("/api/chat", json={"message": "yo"})
    assert r.status_code == 200 and r.get_json() == {"reply": "hi"}


def test_enable_race_keeps_existing_operation_id(state_env):
    first = maintenance.enable("a")
    real = maintenance.status
    calls = {"n": 0}

    def stale_status():
        calls["n"] += 1
        return None if calls["n"] == 1 else real()
    with patch.object(maintenance, "status", stale_status):
        second = maintenance.enable("b")
    assert second["operation_id"] == first["operation_id"]
    assert json.loads((state_env / "maintenance.json").read_text())["operation_id"] == first["operation_id"]


def test_sync_marker_lifecycle_and_staleness(state_env):
    assert maintenance.sync_in_flight() is False
    marker = maintenance.mark_sync_running()
    assert marker.exists() and marker.name.startswith("sync.running.")
    assert maintenance.sync_in_flight() is True
    os.utime(marker, (1, 1))
    assert maintenance.sync_in_flight() is False  # stale
    maintenance.clear_sync_running(marker)
    assert not marker.exists()
    maintenance.clear_sync_running(marker)  # idempotent


def test_overlapping_markers_are_independent(state_env):
    a, b = maintenance.mark_sync_running(), maintenance.mark_sync_running()
    assert a != b
    maintenance.clear_sync_running(a)
    assert b.exists() and maintenance.sync_in_flight() is True
    maintenance.clear_sync_running(b)
    assert maintenance.sync_in_flight() is False


def test_marker_write_failure_is_loud_not_fatal(state_env, caplog):
    with patch("school_dashboard.maintenance.write_atomic", side_effect=OSError("ro fs")):
        with caplog.at_level("ERROR"):
            assert maintenance.mark_sync_running() is None
    assert "sync marker" in caplog.text.lower()


def test_enable_link_failure_is_clean_and_fails_closed(state_env, capsys):
    from school_dashboard import cli
    with patch("school_dashboard.maintenance.os.link", side_effect=PermissionError("nope")):
        with patch("sys.argv", ["school-state", "transition", "maintenance", "on"]):
            with pytest.raises(SystemExit) as ei:
                cli.main()
    assert ei.value.code == 1
    assert "could not enable maintenance" in capsys.readouterr().err.lower()


def test_background_sync_marker_written_before_check(state_env):
    """The marker must exist when maintenance is first checked (closes the race)."""
    m = _fresh_sync_state()
    seen = []
    real = maintenance.is_active

    def spy():
        seen.append(bool(list(state_env.glob("sync.running.*"))))
        return real()
    with patch("web.app.maintenance.is_active", spy), patch("web.app.subprocess.run"):
        maintenance.enable("t")
        m._run_sync_background("sgy", "none")
    assert seen and seen[0] is True
    assert not list(state_env.glob("sync.running*"))


def _fresh_sync_state():
    import web.app as m
    m._sync_lock.acquire()
    m._sync_status.update({"running": False, "last_result": None, "last_error": None})
    return m


def test_background_sync_aborts_when_active(state_env):
    m = _fresh_sync_state()
    maintenance.enable("t")
    with patch("web.app.subprocess.run") as run:
        m._run_sync_background("sgy,gc", "none")
    run.assert_not_called()
    assert m._sync_status["last_result"] == "skipped: maintenance"
    assert m._sync_status["running"] is False
    assert not m._sync_lock.locked()
    assert not list(state_env.glob("sync.running*"))


def test_background_sync_aborts_between_steps(state_env, tmp_path, monkeypatch):
    m = _fresh_sync_state()
    monkeypatch.setenv("SGY_FILE", str(tmp_path / "sgy.json"))

    def flip(*a, **k):
        maintenance.enable("mid")
        return type("R", (), {"stdout": ""})()
    with patch("web.app.subprocess.run", side_effect=flip) as run:
        m._run_sync_background("sgy,gc", "none")
    assert run.call_count == 1
    assert m._sync_status["last_result"] == "skipped: maintenance"


def test_background_sync_marker_present_during_run(state_env, tmp_path, monkeypatch):
    m = _fresh_sync_state()
    monkeypatch.setenv("SGY_FILE", str(tmp_path / "sgy.json"))
    seen = []

    def spy(*a, **k):
        seen.append(bool(list(state_env.glob("sync.running.*"))))
        return type("R", (), {"stdout": ""})()
    with patch("web.app.subprocess.run", side_effect=spy):
        m._run_sync_background("sgy", "none")
    assert seen and all(seen)
    assert not list(state_env.glob("sync.running*"))


def test_write_sync_source_guarded_and_atomic(state_env):
    from school_dashboard import sync_meta
    sync_meta.write_sync_source("ixl", "ok")
    p = state_env / "sync_meta.json"
    assert json.loads(p.read_text())["ixl"]["last_result"] == "ok"
    assert not [f for f in state_env.iterdir() if f.name.endswith(".tmp") or ".tmp" in f.name]
    maintenance.enable("t")
    sync_meta.write_sync_source("sgy", "ok")
    assert "sgy" not in json.loads(p.read_text())


def test_sync_meta_write_failure_leaves_old_file(state_env):
    from school_dashboard import sync_meta
    sync_meta.write_sync_source("ixl", "ok")
    p = state_env / "sync_meta.json"
    before = p.read_text()
    with patch("school_dashboard.atomic.os.replace", side_effect=OSError("boom")):
        sync_meta.write_sync_source("sgy", "ok")
    assert p.read_text() == before
    assert not [f for f in state_env.iterdir() if f.name not in ("sync_meta.json",)]


def test_html_render_and_state_save_guarded(state_env):
    from school_dashboard import html, state
    s = state.load(str(state_env / "s.json"))
    maintenance.enable("t")
    out = state_env / "d.html"
    html.render(s, str(out))
    state.save(s, str(state_env / "s.json"))
    assert not out.exists() and not (state_env / "s.json").exists()


def test_email_digest_write_guarded_and_atomic(state_env):
    from school_dashboard import email
    out = state_env / "email-digest.json"
    email.write_digest({"total": 0}, out)
    assert json.loads(out.read_text()) == {"total": 0}
    maintenance.enable("t")
    email.write_digest({"total": 5}, out)
    assert json.loads(out.read_text()) == {"total": 0}


def test_school_sync_marker_exists_during_first_check(tmp_path):
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, calls = _counting_stub(tmp_path, inactive_calls=0)  # always active
    r = _sync_env_run(tmp_path, bindir, sd)
    assert r.returncode == 0, r.stderr
    assert "check-saw-marker" in calls.read_text()
    assert not list(sd.glob("sync.running*"))


def _gc_stub_dir(tmp_path, inactive_calls):
    bindir, calls = _counting_stub(tmp_path, inactive_calls)
    gc = bindir / "gc"
    gc.write_text("""#!/bin/sh
case "$1" in
  teams) echo '[{"id":"t1","name":"Team One"}]' ;;
  summary) echo '{"schedule":[{"date":"2026-01-01","type":"game"}]}' ;;
esac
""")
    gc.chmod(0o755)
    return bindir, calls


def _gc_run(bindir, sd):
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(sd), "SCHOOL_DASHBOARD_ENV": "/nonexistent",
           "SCHOOL_STATE_DIR": str(sd), "SCHOOL_GC_PATH": str(sd / "gc-schedule.json")}
    return subprocess.run(["bash", str(REPO / "sync/gc-scrape.sh")], env=env,
                          capture_output=True, text=True, timeout=30)


def test_gc_scrape_writes_when_inactive(tmp_path):
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, _ = _gc_stub_dir(tmp_path, inactive_calls=99)
    r = _gc_run(bindir, sd)
    assert r.returncode == 0, r.stderr
    assert json.loads((sd / "gc-schedule.json").read_text())["teams"]


def test_gc_scrape_rechecks_before_final_write(tmp_path):
    sd = tmp_path / "st"
    sd.mkdir()
    bindir, _ = _gc_stub_dir(tmp_path, inactive_calls=1)  # inactive at start, active at write
    r = _gc_run(bindir, sd)
    assert r.returncode == 0, r.stderr
    assert not (sd / "gc-schedule.json").exists()
    assert not [f for f in sd.iterdir() if ".tmp" in f.name]
    assert "maintenance mode" in (sd / "gc-scrape.log").read_text().lower()
