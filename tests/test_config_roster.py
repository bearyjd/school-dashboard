import json
import logging

import pytest

from school_dashboard import state
from web.app import app as flask_app


@pytest.fixture(autouse=True)
def reset_cache():
    state._config_cache = None
    yield
    state._config_cache = None


def _write_config_at(p, data) -> None:
    p.write_text(json.dumps(data))


def _write_config(tmp_path, monkeypatch, data) -> None:
    p = tmp_path / "cfg.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(p))


def test_roster_valid(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"children": {
        "Ford": {"grade": "3rd", "school": "St Mark"},
        "Penn": {"grade": "6th", "school": "St Mark"},
    }})
    assert state.get_roster() == [
        {"id": "ford", "name": "Ford", "grade": "3rd", "school": "St Mark"},
        {"id": "penn", "name": "Penn", "grade": "6th", "school": "St Mark"},
    ]


def test_roster_explicit_id_and_slug_default(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"children": {
        "Mary Jane": {"grade": "1st", "school": "S"},
        "Jack": {"id": "kid-j", "grade": "8th", "school": "S"},
    }})
    ids = {r["name"]: r["id"] for r in state.get_roster()}
    assert ids == {"Mary Jane": "mary-jane", "Jack": "kid-j"}


def test_roster_missing_file(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "nope.json"))
    with caplog.at_level(logging.WARNING):
        assert state.get_roster() == []
        assert state.get_children() == {}
    assert "nope.json" in caplog.text
    assert state._config_cache is None  # failed load not cached


def test_directory_at_config_path(tmp_path, monkeypatch, caplog):
    d = tmp_path / "cfg"
    d.mkdir()
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(d))
    with caplog.at_level(logging.WARNING):
        assert state.get_roster() == []
    assert "cfg" in caplog.text
    assert state._config_cache is None


def test_failed_load_recovers_when_file_appears(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "later.json"))
    assert state.get_roster() == []
    _write_config_at(tmp_path / "later.json", {"children": {"Ford": {"grade": "3rd", "school": "S"}}})
    assert [r["name"] for r in state.get_roster()] == ["Ford"]


def test_get_children_is_lenient(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"children": {
        "NoSchool": {"grade": "4th"},
        "NumGrade": {"grade": 3, "school": "S"},
        "NotDict": "x",
    }})
    children = state.get_children()
    assert children == {"NoSchool": {"grade": "4th"}, "NumGrade": {"grade": 3, "school": "S"}}
    assert state.get_roster() == []


def test_non_dict_name_aliases_ignored(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"children": {}, "name_aliases": ["x"]})
    assert state.get_name_aliases() == {"john": "Jack"}  # config ignored; built-in default remains


def test_roster_malformed_json_logged(tmp_path, monkeypatch, caplog):
    _write_config(tmp_path, monkeypatch, "{not json")
    with caplog.at_level(logging.WARNING):
        assert state.get_roster() == []
    assert "cfg.json" in caplog.text


def test_roster_invalid_entries_skipped_and_logged(tmp_path, monkeypatch, caplog):
    _write_config(tmp_path, monkeypatch, {"children": {
        "Ford": {"grade": "3rd", "school": "S"},
        "BadGrade": {"grade": 3, "school": "S"},
        "NoSchool": {"grade": "4th"},
        "NotDict": "x",
    }})
    with caplog.at_level(logging.WARNING):
        roster = state.get_roster()
    assert [r["name"] for r in roster] == ["Ford"]
    assert "BadGrade" in caplog.text and "NoSchool" in caplog.text and "NotDict" in caplog.text


def test_roster_duplicate_ids_skipped(tmp_path, monkeypatch, caplog):
    _write_config(tmp_path, monkeypatch, {"children": {
        "A": {"id": "same", "grade": "1st", "school": "S"},
        "B": {"id": "same", "grade": "2nd", "school": "S"},
    }})
    with caplog.at_level(logging.WARNING):
        roster = state.get_roster()
    assert [r["name"] for r in roster] == ["A"]
    assert "same" in caplog.text


def test_roster_does_not_mutate_cached_config(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"children": {"Ford": {"grade": "3rd", "school": "S"}}})
    first = state.get_roster()
    first[0]["name"] = "Changed"
    assert state.get_roster()[0]["name"] == "Ford"


def test_cache_reset_picks_up_new_config(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"children": {"Ford": {"grade": "3rd", "school": "S"}}})
    assert len(state.get_roster()) == 1
    _write_config(tmp_path, monkeypatch, {"children": {}})
    assert len(state.get_roster()) == 1  # cached
    state._config_cache = None
    assert state.get_roster() == []


@pytest.fixture
def dash_client(tmp_path, monkeypatch):
    def make(state_data, config):
        sp = tmp_path / "state.json"
        sp.write_text(json.dumps(state_data))
        monkeypatch.setenv("SCHOOL_STATE_PATH", str(sp))
        _write_config(tmp_path, monkeypatch, config)
        flask_app.config["TESTING"] = True
        return flask_app.test_client()
    return make


CONFIG = {"children": {
    "Ford": {"grade": "3rd", "school": "St Mark"},
    "Penn": {"grade": "6th", "school": "St Mark"},
    "Jack": {"grade": "8th", "school": "St Mark"},
}}


def test_dashboard_includes_roster_and_has_data(dash_client):
    c = dash_client({
        "ixl": {"Ford": {"totals": {"math": {"remaining": 0, "assigned": 2, "done": 2}}}},
        "schoology": {"Penn": {"assignments": []}},
    }, CONFIG)
    body = c.get("/api/dashboard").get_json()
    assert [r["name"] for r in body["children"]] == ["Ford", "Penn", "Jack"]
    assert body["children"][0] == {"id": "ford", "name": "Ford", "grade": "3rd", "school": "St Mark"}
    assert body["has_data"]["Ford"] == {"ixl": True, "sgy": False}
    assert body["has_data"]["Penn"] == {"ixl": False, "sgy": True}
    assert body["ixl"] == {} and body["schoology"] == {}  # existing keys unchanged


def test_dashboard_never_scraped_child(dash_client):
    c = dash_client({"ixl": {}, "schoology": {}}, CONFIG)
    body = c.get("/api/dashboard").get_json()
    assert body["has_data"]["Jack"] == {"ixl": False, "sgy": False}


def test_dashboard_has_data_uses_alias_normalization(dash_client):
    c = dash_client({"ixl": {"John": {"totals": {}}}, "schoology": {}}, CONFIG)
    body = c.get("/api/dashboard").get_json()
    assert body["has_data"]["Jack"]["ixl"] is True


def test_dashboard_has_data_requires_non_empty_dict(dash_client):
    c = dash_client({"ixl": {"Ford": {}, "Penn": {}}, "schoology": {"Jack": {}}}, CONFIG)
    body = c.get("/api/dashboard").get_json()
    for name in ("Ford", "Penn", "Jack"):
        assert body["has_data"][name] == {"ixl": False, "sgy": False}


def test_dashboard_empty_roster(dash_client):
    c = dash_client({"ixl": {}, "schoology": {}}, {"children": {}})
    body = c.get("/api/dashboard").get_json()
    assert body["children"] == []


def test_dashboard_tolerates_non_dict_child_entries(dash_client):
    c = dash_client({
        "ixl": {"Ford": "error: login failed", "Penn": None,
                "Jack": {"totals": {"math": {"remaining": 3, "assigned": 5, "done": 2}, "bad": "x"}}},
        "schoology": {"Ford": ["oops"], "Penn": {"assignments": ["junk", {"title": "HW"}]}},
    }, CONFIG)
    resp = c.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ixl"]["Jack"] == [{"subject": "math", "remaining": 3, "assigned": 5, "done": 2}]
    assert [a["title"] for a in body["schoology"]["Penn"]] == ["HW"]
