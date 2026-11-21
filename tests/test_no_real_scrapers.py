"""The conftest guard: no test can start a real sgy/ixl/powerschool/gc."""
import subprocess

import pytest

from school_dashboard.scrape_run import run_scraper


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


def test_scraper_scripts_are_guarded(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    with pytest.raises(AssertionError, match="real scraper: gc-scrape.sh"):
        subprocess.run(["bash", "/somewhere/sync/gc-scrape.sh"], timeout=1, check=False)


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
