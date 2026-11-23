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


def test_no_alias_without_config_aliases(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}})
    assert _normalize_child("Caseyson") == "Caseyson"
    assert _normalize_child("Avery") == "Avery"


def test_no_alias_without_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "missing.json"))
    assert _normalize_child("Caseyson") == "Caseyson"


def test_config_alias_is_honored(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}, "name_aliases": {"blairton": "Blair"}})
    assert _normalize_child("Blairton") == "Blair"
    assert _normalize_child("blairton") == "Blair"


def test_config_alias_is_case_insensitive(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}, "name_aliases": {"CaSeYsOn": "Casey"}})
    assert _normalize_child("Caseyson") == "Casey"


def test_state_and_app_agree_on_configured_alias(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}, "name_aliases": {"caseyson": "Casey"}})
    assert state._canonicalize("Caseyson") == "Casey" == _normalize_child("Caseyson")
    assert state._canonicalize("caseyson ") == "Casey" == _normalize_child("caseyson ")


def test_normalize_child_matches_canonicalize_for_unmapped(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}})
    assert _normalize_child("avery") == state._canonicalize("avery") == "Avery"


def test_normalize_child_tolerates_none_and_empty(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"children": {}})
    assert _normalize_child(None) == ""
    assert _normalize_child("") == ""
