"""Tests for per-source toggles (python helper + shell helper in sync/school-sync.sh)."""
import re
import subprocess
from pathlib import Path

import pytest

from school_dashboard.sources import source_enabled

SCRIPT = Path(__file__).resolve().parent.parent / "sync" / "school-sync.sh"


def test_default_enabled(monkeypatch):
    monkeypatch.delenv("SOURCE_IXL_ENABLED", raising=False)
    assert source_enabled("ixl") is True


@pytest.mark.parametrize("val", ["0", "false", "FALSE", "No", "off", " Off "])
def test_falsy_values_disable(monkeypatch, val):
    monkeypatch.setenv("SOURCE_SGY_ENABLED", val)
    assert source_enabled("sgy") is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", ""])
def test_truthy_or_empty_enable(monkeypatch, val):
    monkeypatch.setenv("SOURCE_GC_ENABLED", val)
    assert source_enabled("gc") is True


def test_sources_are_independent(monkeypatch):
    monkeypatch.setenv("SOURCE_IXL_ENABLED", "off")
    monkeypatch.delenv("SOURCE_SGY_ENABLED", raising=False)
    assert source_enabled("ixl") is False
    assert source_enabled("sgy") is True


def _shell_helper() -> str:
    m = re.search(r"^source_enabled\(\) \{.*?^\}", SCRIPT.read_text(), re.S | re.M)
    assert m, "source_enabled() not found in school-sync.sh"
    return m.group(0)


def _shell(name: str, env: dict) -> int:
    snippet = f'{_shell_helper()}\nsource_enabled {name}'
    return subprocess.run(["bash", "-euo", "pipefail", "-c", snippet], env=env).returncode


def test_shell_default_enabled():
    assert _shell("ixl", {}) == 0


@pytest.mark.parametrize("val", ["0", "false", "FALSE", "No", "off"])
def test_shell_falsy_disables(val):
    assert _shell("gc", {"SOURCE_GC_ENABLED": val}) == 1


def test_shell_truthy_enables():
    assert _shell("sgy", {"SOURCE_SGY_ENABLED": "1"}) == 0
