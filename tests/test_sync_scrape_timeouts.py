"""On-demand sync worker: configurable scraper timeouts, group kill, per-source reporting.

Real fake CLIs on PATH (sgy / ixl / powerschool / school-state); no network, no credentials.
"""
import json
import os
import signal
import time
from pathlib import Path

import pytest

import web.app as app_module
from school_dashboard.scrape_run import ScrapeRun
from school_dashboard.sync_meta import read_sync_meta

IXL_JSON = json.dumps({"totals": {"Math": {}}, "remaining": []})
SGY_JSON = json.dumps({"children": {"alpha": {}}})
PS_JSON = json.dumps({"children": {}})
HANG = 'sleep 300 &\necho $! > "$FAKE_DIR/{name}.child"\nsleep 300\n'


def _stub(bindir: Path, name: str, body: str) -> None:
    f = bindir / name
    f.write_text(f"#!/bin/bash\n{body}")
    f.chmod(0o755)


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


@pytest.fixture
def world(tmp_path, monkeypatch, state_env):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = tmp_path / "fake"
    fake.mkdir()
    (tmp_path / "ixl").mkdir()
    _stub(bindir, "ixl", f"echo '{IXL_JSON}'\n")
    _stub(bindir, "sgy", f"echo '{SGY_JSON}'\n")
    _stub(bindir, "powerschool", f"echo '{PS_JSON}'\n")
    _stub(bindir, "school-state", 'echo "school-state $*" >> "$FAKE_DIR/calls.log"\n')
    for key, val in {
        "PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path), "FAKE_DIR": str(fake),
        "IXL_DIR": str(tmp_path / "ixl"), "SGY_FILE": str(tmp_path / "sgy.json"),
        "PS_FILE": str(tmp_path / "ps.json"), "PS_USERNAME": "alpha", "IXL_CRON": "",
        "IXL_EMAIL": "alpha@school",
    }.items():
        monkeypatch.setenv(key, val)
    for var in ("SGY_SCRAPE_TIMEOUT", "POWERSCHOOL_SCRAPE_TIMEOUT", "IXL_SCRAPE_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "sgy.json").write_text("PREVIOUS-SGY")
    (tmp_path / "ps.json").write_text("PREVIOUS-PS")
    app_module._sync_status.update({"last_result": None, "last_error": None, "last_timeouts": []})
    yield tmp_path, bindir, fake
    for child in fake.glob("*.child"):
        try:
            os.kill(int(child.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def _sync(sources: str) -> None:
    assert app_module._sync_lock.acquire(blocking=False)
    app_module._run_sync_background(sources, "none")


def _calls(fake: Path) -> str:
    f = fake / "calls.log"
    return f.read_text() if f.exists() else ""


@pytest.mark.parametrize("src,var,hang_file", [
    ("sgy", "SGY_SCRAPE_TIMEOUT", "sgy.json"), ("powerschool", "POWERSCHOOL_SCRAPE_TIMEOUT", "ps.json"),
])
def test_hung_scraper_is_killed_reported_and_keeps_previous_data(world, monkeypatch, src, var, hang_file):
    tmp, bindir, fake = world
    _stub(bindir, src, HANG.format(name=src))
    monkeypatch.setenv(var, "1")
    start = time.monotonic()
    _sync(f"ixl,{src}")
    assert time.monotonic() - start < 30
    status = app_module._sync_status
    assert status["last_result"] == "error"
    assert src in status["last_error"] and "timed out" in status["last_error"]
    assert status["last_timeouts"] == [src]
    meta = read_sync_meta()
    assert meta[src]["last_result"] == "error" and meta[src]["last_issue"] == "timeout"
    assert meta["ixl"]["last_result"] == "ok" and "last_issue" not in meta["ixl"]
    assert (tmp / hang_file).read_text().startswith("PREVIOUS")
    assert "school-state update" in _calls(fake) and "school-state html" in _calls(fake)
    assert not _alive(int((fake / f"{src}.child").read_text()))


def test_next_ok_run_clears_the_timeout_issue(world, monkeypatch):
    _, bindir, fake = world
    good = (bindir / "sgy").read_text()
    _stub(bindir, "sgy", HANG.format(name="sgy"))
    monkeypatch.setenv("SGY_SCRAPE_TIMEOUT", "1")
    _sync("sgy")
    assert read_sync_meta()["sgy"]["last_issue"] == "timeout"
    (bindir / "sgy").write_text(good)
    _sync("sgy")
    assert read_sync_meta()["sgy"]["last_result"] == "ok"
    assert "last_issue" not in read_sync_meta()["sgy"]
    assert app_module._sync_status["last_timeouts"] == []
    assert app_module._sync_status["last_error"] is None


def test_defaults_are_passed_to_the_scrapers(world, monkeypatch):
    seen: dict[str, float] = {}

    def fake_run(cmd, *, timeout, env=None):
        seen[cmd[0]] = timeout
        out = {"ixl": IXL_JSON, "sgy": SGY_JSON, "powerschool": PS_JSON}[cmd[0]]
        return ScrapeRun(returncode=0, stdout=out, stderr="", timed_out=False)

    monkeypatch.setattr(app_module, "run_scraper", fake_run)
    _sync("ixl,sgy,powerschool")
    assert seen == {"ixl": 120.0, "sgy": 900.0, "powerschool": 900.0}


def test_env_override_is_passed_to_the_scraper(world, monkeypatch):
    seen: dict[str, float] = {}

    def fake_run(cmd, *, timeout, env=None):
        seen[cmd[0]] = timeout
        return ScrapeRun(returncode=0, stdout=SGY_JSON, stderr="", timed_out=False)

    monkeypatch.setattr(app_module, "run_scraper", fake_run)
    monkeypatch.setenv("SGY_SCRAPE_TIMEOUT", "1234")
    _sync("sgy")
    assert seen["sgy"] == 1234.0


@pytest.mark.parametrize("src", ["sgy", "powerschool"])
def test_slow_scraper_under_the_default_is_not_killed(world, src):
    tmp, bindir, _ = world
    out = SGY_JSON if src == "sgy" else PS_JSON
    _stub(bindir, src, f"sleep 2\necho '{out}'\n")
    _sync(src)
    assert app_module._sync_status["last_result"] == "ok"
    assert app_module._sync_status["last_timeouts"] == []
    assert read_sync_meta()[src]["last_result"] == "ok"
    assert (tmp / ("sgy.json" if src == "sgy" else "ps.json")).read_text().strip() == out


def test_ixl_child_timeout_is_reported_as_a_timeout(world, monkeypatch):
    _, bindir, fake = world
    _stub(bindir, "ixl", HANG.format(name="ixl"))
    monkeypatch.setenv("IXL_SCRAPE_TIMEOUT", "1")
    _sync("ixl")
    assert app_module._sync_status["last_timeouts"] == ["ixl"]
    assert read_sync_meta()["ixl"]["last_issue"] == "timeout"
    assert not _alive(int((fake / "ixl.child").read_text()))


# --- failed start / non-zero exit / bad output is an ERROR, previous data kept ----------------

def _no_binary(tmp, bindir, monkeypatch, name):
    """The binary cannot start: run_scraper's own failed-start result (rc 127)."""
    (bindir / name).unlink()

    def failed_start(cmd, *, timeout, env=None, **_kw):
        if cmd[0] != name:
            return ScrapeRun(0, {"ixl": IXL_JSON, "sgy": SGY_JSON}.get(cmd[0], PS_JSON), "", False)
        return ScrapeRun(127, "", f"failed to start {name}: [Errno 2] No such file or directory", False)

    monkeypatch.setattr(app_module, "run_scraper", failed_start)


def _assert_error_kept(tmp, src, previous_file):
    assert app_module._sync_status["last_result"] == "error"
    assert read_sync_meta()[src]["last_result"] == "error"
    assert "last_issue" not in read_sync_meta()[src]
    assert app_module._sync_status["last_timeouts"] == []
    assert (tmp / previous_file).read_text().startswith("PREVIOUS")


SGY_BAD = {
    "rc2_with_stdout": f"echo '{SGY_JSON}'\nexit 2\n",
    "empty_stdout": "exit 0\n",
    "invalid_json": "printf '{\"trunc'\n",
    "json_array": "echo '[1, 2]'\n",
}


@pytest.mark.parametrize("case", list(SGY_BAD))
def test_sgy_bad_run_is_an_error_and_keeps_previous_file(world, case):
    tmp, bindir, _ = world
    _stub(bindir, "sgy", SGY_BAD[case])
    _sync("sgy")
    _assert_error_kept(tmp, "sgy", "sgy.json")


def test_sgy_missing_binary_is_an_error(world, monkeypatch):
    tmp, bindir, _ = world
    _no_binary(tmp, bindir, monkeypatch, "sgy")
    _sync("sgy")
    _assert_error_kept(tmp, "sgy", "sgy.json")


def test_sgy_valid_json_is_written_and_ok(world):
    tmp, _, _ = world
    _sync("sgy")
    assert read_sync_meta()["sgy"]["last_result"] == "ok"
    assert json.loads((tmp / "sgy.json").read_text()) == json.loads(SGY_JSON)


def test_powerschool_missing_binary_is_an_error(world, monkeypatch):
    tmp, bindir, _ = world
    _no_binary(tmp, bindir, monkeypatch, "powerschool")
    monkeypatch.setattr(app_module.powerschool, "skip_reason", lambda: None)  # configured, binary vanished
    _sync("powerschool")
    _assert_error_kept(tmp, "powerschool", "ps.json")


def test_powerschool_nonzero_exit_is_an_error(world):
    tmp, bindir, _ = world
    _stub(bindir, "powerschool", f"echo '{PS_JSON}'\nexit 2\n")
    _sync("powerschool")
    _assert_error_kept(tmp, "powerschool", "ps.json")


def test_ixl_missing_binary_is_an_error(world, monkeypatch):
    tmp, bindir, _ = world
    _no_binary(tmp, bindir, monkeypatch, "ixl")
    _sync("ixl")
    assert app_module._sync_status["last_result"] == "error"
    assert read_sync_meta()["ixl"]["last_result"] == "error"
    assert "last_issue" not in read_sync_meta()["ixl"]


def test_ixl_nonzero_exit_is_an_error(world):
    _, bindir, _ = world
    _stub(bindir, "ixl", f"echo '{IXL_JSON}'\nexit 2\n")
    _sync("ixl")
    assert read_sync_meta()["ixl"]["last_result"] == "error"
    assert not list(Path(os.environ["IXL_DIR"]).glob("*-assigned.json"))


# --- combinations -------------------------------------------------------------------------------

def test_sgy_and_powerschool_both_time_out_in_one_run(world, monkeypatch):
    tmp, bindir, fake = world
    _stub(bindir, "sgy", HANG.format(name="sgy"))
    _stub(bindir, "powerschool", HANG.format(name="powerschool"))
    monkeypatch.setenv("SGY_SCRAPE_TIMEOUT", "1")
    monkeypatch.setenv("POWERSCHOOL_SCRAPE_TIMEOUT", "1")
    _sync("ixl,sgy,powerschool")
    status = app_module._sync_status
    assert status["last_timeouts"] == ["sgy", "powerschool"]
    assert "sgy: timed out" in status["last_error"] and "powerschool: timed out" in status["last_error"]
    meta = read_sync_meta()
    for src in ("sgy", "powerschool"):
        assert meta[src]["last_result"] == "error" and meta[src]["last_issue"] == "timeout"
    assert meta["ixl"]["last_result"] == "ok"
    assert "school-state update" in _calls(fake)
    assert (tmp / "sgy.json").read_text().startswith("PREVIOUS")
    assert (tmp / "ps.json").read_text().startswith("PREVIOUS")


def test_last_timeouts_reset_at_start_of_next_run_and_after_worker_exception(world, monkeypatch):
    _, bindir, _ = world
    app_module._sync_status["last_timeouts"] = ["sgy"]
    seen_at_start: list = []

    def boom(*_a, **_k):
        seen_at_start.append(list(app_module._sync_status["last_timeouts"]))
        raise RuntimeError("worker blew up")

    monkeypatch.setattr(app_module, "_scrape_powerschool", boom)
    monkeypatch.setattr(app_module.powerschool, "skip_reason", lambda: None)
    _sync("powerschool")
    assert seen_at_start == [[]]
    assert app_module._sync_status["last_timeouts"] == []
    # a timeout run followed by a clean one starts from []
    _stub(bindir, "sgy", HANG.format(name="sgy"))
    monkeypatch.setenv("SGY_SCRAPE_TIMEOUT", "1")
    _sync("sgy")
    assert app_module._sync_status["last_timeouts"] == ["sgy"]
    monkeypatch.setattr(app_module, "_scrape_powerschool", boom)
    _sync("powerschool")
    assert app_module._sync_status["last_timeouts"] == []


@pytest.mark.parametrize("src,previous", [("sgy", "sgy.json"), ("powerschool", "ps.json"), ("ixl", None)])
def test_truncated_output_is_an_error_output_too_large(world, monkeypatch, src, previous, caplog):
    tmp, _, _ = world
    out = {"sgy": SGY_JSON, "powerschool": PS_JSON, "ixl": IXL_JSON}[src]

    def fake_run(cmd, *, timeout, env=None):
        return ScrapeRun(returncode=0, stdout=out, stderr="", timed_out=False, truncated=True)

    monkeypatch.setattr(app_module, "run_scraper", fake_run)
    monkeypatch.setattr(app_module.powerschool, "skip_reason", lambda: None)
    with caplog.at_level("WARNING"):
        _sync(src)
    assert app_module._sync_status["last_result"] == "error"
    assert read_sync_meta()[src]["last_result"] == "error"
    assert "too large" in caplog.text
    if previous:
        assert (tmp / previous).read_text().startswith("PREVIOUS")
    else:
        assert not list(Path(os.environ["IXL_DIR"]).glob("*-assigned.json"))
