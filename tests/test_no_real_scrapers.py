"""The conftest guard: no test can start a real sgy/ixl/powerschool/gc."""
import os
import shutil
import subprocess
import tempfile

import pytest

from school_dashboard.scrape_run import run_scraper

BASH = shutil.which("bash") or "/bin/bash"


def test_unrouted_scraper_raises_and_starts_nothing(monkeypatch, tmp_path):
    marker = tmp_path / "started"
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # whatever is installed, it is outside tmp
    with pytest.raises(AssertionError, match="test tried to start a real scraper: powerschool"):
        run_scraper(["powerschool", "summary", "--json"], timeout=1)
    assert not marker.exists()


@pytest.mark.parametrize("name", ["sgy", "ixl", "gc"])
def test_each_scraper_name_is_guarded_through_subprocess_run(name, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    with pytest.raises(AssertionError, match=f"real scraper: {name}"):
        subprocess.run([name, "x"], timeout=1, check=False)


@pytest.fixture
def outside_bin():
    """A directory OUTSIDE the pytest tmp dir holding fake sgy/ixl/powerschool/gc binaries.

    It stands in for "a real scraper is installed", so these tests do not depend on what the
    machine running them happens to have (CI has none of the CLIs; a dev machine may have all).
    """
    root = tempfile.mkdtemp(prefix="outside-scrapers-")
    try:
        for name in ("sgy", "ixl", "powerschool", "gc"):
            path = os.path.join(root, name)
            with open(path, "w") as fh:
                fh.write("#!/bin/sh\necho REAL-STARTED\n")
            os.chmod(path, 0o755)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_outside_bin_is_really_outside_the_pytest_tmp_dir(outside_bin, tmp_path_factory):
    base = os.path.realpath(str(tmp_path_factory.getbasetemp()))
    assert not os.path.realpath(outside_bin).startswith(base)


@pytest.mark.parametrize("script,cli", [("gc-scrape.sh", "gc"), ("ixl-cron.sh", "ixl")])
def test_scraper_scripts_are_guarded_when_their_cli_is_installed(script, cli, outside_bin, tmp_path):
    marker = tmp_path / "ran"
    body = tmp_path / script
    body.write_text(f"touch {marker}\n")
    with pytest.raises(AssertionError, match=f"real scraper: {script}"):
        subprocess.run([BASH, str(body)], env={"PATH": f"{outside_bin}:/usr/bin:/bin"}, timeout=5, check=False)
    assert not marker.exists()


def test_sync_script_is_guarded_when_any_scraper_is_installed(outside_bin, tmp_path):
    body = tmp_path / "school-sync.sh"
    body.write_text("exit 0\n")
    with pytest.raises(AssertionError, match="real scraper: school-sync.sh"):
        subprocess.run([BASH, str(body)], env={"PATH": f"{outside_bin}:/usr/bin:/bin"}, timeout=5, check=False)


@pytest.mark.parametrize("name", ["sgy", "ixl", "powerschool", "gc"])
def test_cli_installed_outside_tmp_is_guarded(name, outside_bin):
    with pytest.raises(AssertionError, match=f"real scraper: {name}"):
        subprocess.run([name, "x"], env={"PATH": f"{outside_bin}:/usr/bin:/bin"}, timeout=5, check=False)


@pytest.mark.parametrize("name", ["sgy", "ixl", "powerschool", "gc"])
def test_cli_that_is_not_installed_anywhere_is_still_guarded(name, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AssertionError, match=f"real scraper: {name}"):
        subprocess.run([name, "x"], env={"PATH": str(empty)}, timeout=5, check=False)


def test_script_whose_clis_resolve_nowhere_may_run(tmp_path):
    """Nothing real can start when no scraper CLI is on the child's PATH (the "CLI missing" tests rely on it)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    body = tmp_path / "gc-scrape.sh"
    body.write_text("echo ran\n")
    res = subprocess.run([BASH, str(body)], env={"PATH": str(empty)}, capture_output=True, text=True, timeout=5)
    assert res.stdout.strip() == "ran"


def test_fake_inside_tmp_path_runs(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "powerschool"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\necho \'{"ok": 1}\'\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    res = run_scraper(["powerschool", "summary", "--json"], timeout=5)
    assert res.returncode == 0 and res.stdout.strip() == '{"ok": 1}'


def test_fake_first_in_explicit_env_path_runs_and_script_with_fake_runs(tmp_path):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    for n in ("gc", "ixl"):
        (fakebin / n).write_text("#!/bin/sh\necho fake\n")
        (fakebin / n).chmod(0o755)
    script = tmp_path / "gc-scrape.sh"
    script.write_text("gc teams\n")
    res = subprocess.run(["bash", str(script)], env={"PATH": f"{fakebin}:/usr/bin:/bin"},
                         capture_output=True, text=True, timeout=5)
    assert res.stdout.strip() == "fake"


def test_non_scraper_commands_unaffected():
    assert subprocess.run(["true"], timeout=5).returncode == 0
