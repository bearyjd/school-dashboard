"""requirements.lock must pin every direct dependency, and the Dockerfile must use it."""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
LOCK_LINE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._+!-]*)$"
)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_lines() -> list[str]:
    lines = (ROOT / "requirements.lock").read_text().splitlines()
    return [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def _lock() -> dict[str, str]:
    return {
        _norm(m.group(1)): m.group(2) for m in map(LOCK_LINE.match, _lock_lines()) if m
    }


def _direct_requirements() -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    reqs = list(project["dependencies"])
    # The image installs the server extra; browser extras live in the vendored scrapers.
    reqs += project["optional-dependencies"]["server"]
    return reqs


def test_lock_lines_are_well_formed():
    bad = [ln for ln in _lock_lines() if not LOCK_LINE.match(ln)]
    assert not bad, f"not name==version: {bad}"


def test_lock_has_no_duplicate_names():
    names = [_norm(m.group(1)) for ln in _lock_lines() if (m := LOCK_LINE.match(ln))]
    assert len(names) == len(set(names))


def test_lock_excludes_local_projects():
    assert not {"school-dashboard", "ixl", "sgy", "gc", "powerschool"} & set(_lock())


@pytest.mark.parametrize("requirement", _direct_requirements())
def test_direct_dependency_pinned_and_satisfied(requirement):
    req = Requirement(requirement)
    lock = _lock()
    assert _norm(req.name) in lock, f"{req.name} missing from requirements.lock"
    assert req.specifier.contains(Version(lock[_norm(req.name)]), prereleases=True), (
        f"{req.name}=={lock[_norm(req.name)]} violates {req.specifier}"
    )


def test_playwright_is_pinned():
    # `playwright install chromium` in the Dockerfile downloads the browser matching this version.
    assert "playwright" in _lock()


def test_dockerfile_uses_lock_and_pins_base_image():
    text = (ROOT / "Dockerfile").read_text()
    assert re.search(r"pip install[^\n]*-c requirements\.lock", text)
    assert re.search(r"^COPY pyproject\.toml requirements\.lock", text, re.M)
    from_line = next(ln for ln in text.splitlines() if ln.startswith("FROM "))
    assert re.search(r"@sha256:[0-9a-f]{64}$", from_line), from_line
