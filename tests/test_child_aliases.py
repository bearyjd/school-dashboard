import json

import pytest

from school_dashboard import state
from web.app import _normalize_child


@pytest.fixture(autouse=True)
def reset_cache():
    state._config_cache = None
    yield
    state._config_cache = None


def _config(tmp_path, monkeypatch, data) -> None:
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(data))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(p))


def test_builtin_default_without_config_aliases(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}})
    assert _normalize_child("John") == "Jack"
    assert _normalize_child("Ford") == "Ford"


def test_builtin_default_without_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "missing.json"))
    assert _normalize_child("John") == "Jack"


def test_config_alias_is_honored(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}, "name_aliases": {"jonny": "Penn"}})
    assert _normalize_child("Jonny") == "Penn"
    assert _normalize_child("jonny") == "Penn"


def test_config_alias_overrides_builtin(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}, "name_aliases": {"john": "Johnny"}})
    assert _normalize_child("John") == "Johnny"


def test_state_and_app_agree_on_builtin_default(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}})
    assert state._canonicalize("John") == "Jack" == _normalize_child("John")
    assert state._canonicalize("john ") == "Jack" == _normalize_child("john ")


def test_normalize_child_matches_canonicalize_for_unmapped(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}})
    assert _normalize_child("ford") == state._canonicalize("ford") == "Ford"


def test_normalize_child_tolerates_none_and_empty(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}})
    assert _normalize_child(None) == ""
    assert _normalize_child("") == ""
