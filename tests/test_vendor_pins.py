"""Guard the vendored scraper submodules (no network).

The Dockerfile COPYs vendor/*, so an uninitialised (empty) submodule dir would silently
ship an image without that scraper. These tests fail on an empty/uninitialised
submodule, and (when the package is installed) on an installed version that differs
from the one declared in the vendor's own pyproject.toml.

Set VENDOR_PINS_STRICT=1 (CI does, after installing the vendors) to turn "package not
installed" / "installed from somewhere other than vendor/" into failures instead of skips.
"""
import os
import re
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
STRICT = os.environ.get("VENDOR_PINS_STRICT") == "1"
# submodule dir -> distribution name declared in its pyproject.toml
SCRAPERS = {
    "ixl-scrape": "ixl",
    "schoology-scrape": "sgy",
    "gc": "gc",
    "powerschool-scrape": "powerschool-scrape",
}


def _declared(directory: str) -> tuple[str, str]:
    text = (VENDOR / directory / "pyproject.toml").read_text(encoding="utf-8")
    project = text.split("[project]", 1)[1]
    name = re.search(r'^name\s*=\s*"([^"]+)"', project, re.M)
    version = re.search(r'^version\s*=\s*"([^"]+)"', project, re.M)
    assert name and version, f"vendor/{directory}/pyproject.toml has no [project] name/version"
    return name.group(1), version.group(1)


def test_gitmodules_match_the_expected_scrapers():
    text = (ROOT / ".gitmodules").read_text(encoding="utf-8")
    assert set(re.findall(r'path\s*=\s*vendor/(\S+)', text)) == set(SCRAPERS)


@pytest.mark.parametrize("directory", sorted(SCRAPERS))
def test_submodule_is_initialised(directory):
    path = VENDOR / directory
    assert path.is_dir() and any(path.iterdir()), (
        f"vendor/{directory} is empty: run `git submodule update --init` "
        "(the Docker image would ship without this scraper)"
    )
    assert (path / "pyproject.toml").is_file(), f"vendor/{directory}/pyproject.toml missing"
    assert _declared(directory)[0] == SCRAPERS[directory]


@pytest.mark.parametrize("directory", sorted(SCRAPERS))
def test_installed_version_matches_vendor_pyproject(directory):
    name, declared = _declared(directory)
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        if STRICT:
            pytest.fail(f"{name} is not installed (pip install -e vendor/{directory})")
        pytest.skip(f"{name} not installed")
    assert dist.version == declared, (
        f"installed {name} {dist.version} != vendor/{directory}/pyproject.toml {declared}"
    )
    if STRICT:
        direct_url = dist.read_text("direct_url.json") or ""
        assert f"vendor/{directory}" in direct_url, (
            f"{name} is installed from outside vendor/{directory}: {direct_url.strip()}"
        )
