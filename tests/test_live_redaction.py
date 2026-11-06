"""Prove tests/e2e/live/conftest.py keeps scraped data out of failing-run output."""
import shutil
import subprocess
import sys
from pathlib import Path

CONFTEST = Path(__file__).parent / "e2e" / "live" / "conftest.py"
SECRET = "ZZ_SECRET_NAME"

LEAKY_TESTS = f'''
import pytest
pytestmark = pytest.mark.live
DATA = {{"children": [{{"name": "{SECRET}", "missing": []}}]}}

def test_assert_introspection():
    names = [c["name"] for c in DATA["children"]]
    assert names == [], "custom message"

def test_keyerror():
    DATA["children"][0]["{SECRET}"]

def test_printed_and_local():
    secret_local = DATA["children"][0]["name"]
    print(secret_local)
    assert secret_local == "other"

def test_fail_with_count_only():
    pytest.fail("1 warning(s)")
'''


def _run(tmp_path: Path, with_conftest: bool) -> str:
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = live: x\n")
    (tmp_path / "test_leaky.py").write_text(LEAKY_TESTS)
    if with_conftest:
        shutil.copy(CONFTEST, tmp_path / "conftest.py")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-m", "live", "-l", "-q", "--tb=long"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    return proc.stdout + proc.stderr


def test_control_run_without_conftest_leaks(tmp_path):
    # Guards the guard: the fixture tests really do leak when unprotected.
    assert SECRET in _run(tmp_path, with_conftest=False)


def test_redacted_run_never_shows_secret(tmp_path):
    out = _run(tmp_path, with_conftest=True)
    assert SECRET not in out
    assert "4 failed" in out
    assert "1 warning(s)" in out
