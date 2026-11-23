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
        "Avery": {"grade": "4th", "school": "Example School"},
        "Blair": {"grade": "7th", "school": "Example School"},
    }})
    assert state.get_roster() == [
        {"id": "avery", "name": "Avery", "grade": "4th", "school": "Example School"},
        {"id": "blair", "name": "Blair", "grade": "7th", "school": "Example School"},
    ]


def test_roster_explicit_id_and_slug_default(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"children": {
        "Mary Jane": {"grade": "1st", "school": "S"},
        "Casey": {"id": "kid-c", "grade": "9th", "school": "S"},
    }})
    ids = {r["name"]: r["id"] for r in state.get_roster()}
    assert ids == {"Mary Jane": "mary-jane", "Casey": "kid-c"}


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
    _write_config_at(tmp_path / "later.json", {"children": {"Avery": {"grade": "4th", "school": "S"}}})
    assert [r["name"] for r in state.get_roster()] == ["Avery"]


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
    assert state.get_name_aliases() == {}


def test_roster_malformed_json_logged(tmp_path, monkeypatch, caplog):
    _write_config(tmp_path, monkeypatch, "{not json")
    with caplog.at_level(logging.WARNING):
        assert state.get_roster() == []
    assert "cfg.json" in caplog.text


def test_roster_invalid_entries_skipped_and_logged(tmp_path, monkeypatch, caplog):
    _write_config(tmp_path, monkeypatch, {"children": {
        "Avery": {"grade": "4th", "school": "S"},
        "BadGrade": {"grade": 3, "school": "S"},
        "NoSchool": {"grade": "4th"},
        "NotDict": "x",
    }})
    with caplog.at_level(logging.WARNING):
        roster = state.get_roster()
    assert [r["name"] for r in roster] == ["Avery"]
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
    _write_config(tmp_path, monkeypatch, {"children": {"Avery": {"grade": "4th", "school": "S"}}})
    first = state.get_roster()
    first[0]["name"] = "Changed"
    assert state.get_roster()[0]["name"] == "Avery"


def test_cache_reset_picks_up_new_config(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"children": {"Avery": {"grade": "4th", "school": "S"}}})
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
    "Avery": {"grade": "4th", "school": "Example School"},
    "Blair": {"grade": "7th", "school": "Example School"},
    "Casey": {"grade": "9th", "school": "Example School"},
}}


def test_dashboard_includes_roster_and_has_data(dash_client):
    c = dash_client({
        "ixl": {"Avery": {"totals": {"math": {"remaining": 0, "assigned": 2, "done": 2}}}},
        "schoology": {"Blair": {"assignments": []}},
    }, CONFIG)
    body = c.get("/api/dashboard").get_json()
    assert [r["name"] for r in body["children"]] == ["Avery", "Blair", "Casey"]
    assert body["children"][0] == {"id": "avery", "name": "Avery", "grade": "4th", "school": "Example School"}
    assert body["has_data"]["Avery"] == {"ixl": True, "sgy": False, "ps": False}
    assert body["has_data"]["Blair"] == {"ixl": False, "sgy": True, "ps": False}
    assert body["ixl"] == {} and body["schoology"] == {}  # existing keys unchanged


def test_dashboard_never_scraped_child(dash_client):
    c = dash_client({"ixl": {}, "schoology": {}}, CONFIG)
    body = c.get("/api/dashboard").get_json()
    assert body["has_data"]["Casey"] == {"ixl": False, "sgy": False, "ps": False}


def test_dashboard_has_data_uses_alias_normalization(dash_client):
    c = dash_client({"ixl": {"Caseyson": {"totals": {}}}, "schoology": {}},
                    {**CONFIG, "name_aliases": {"caseyson": "Casey"}})
    body = c.get("/api/dashboard").get_json()
    assert body["has_data"]["Casey"]["ixl"] is True


def test_dashboard_has_data_requires_non_empty_dict(dash_client):
    c = dash_client({"ixl": {"Avery": {}, "Blair": {}}, "schoology": {"Casey": {}}}, CONFIG)
    body = c.get("/api/dashboard").get_json()
    for name in ("Avery", "Blair", "Casey"):
        assert body["has_data"][name] == {"ixl": False, "sgy": False, "ps": False}


def test_dashboard_empty_roster(dash_client):
    c = dash_client({"ixl": {}, "schoology": {}}, {"children": {}})
    body = c.get("/api/dashboard").get_json()
    assert body["children"] == []


def test_dashboard_tolerates_non_dict_child_entries(dash_client):
    c = dash_client({
        "ixl": {"Avery": "error: login failed", "Blair": None,
                "Casey": {"totals": {"math": {"remaining": 3, "assigned": 5, "done": 2}, "bad": "x"}}},
        "schoology": {"Avery": ["oops"], "Blair": {"assignments": ["junk", {"title": "HW"}]}},
    }, CONFIG)
    resp = c.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ixl"]["Casey"] == [{"subject": "math", "remaining": 3, "assigned": 5, "done": 2}]
    assert [a["title"] for a in body["schoology"]["Blair"]] == ["HW"]


def test_dashboard_children_use_canonical_names_matching_has_data(dash_client):
    cfg = {"children": {"Samantha": {"grade": "5th", "school": "Example School"}, "Sam": {"grade": "9th", "school": "Example School"}},
           "name_aliases": {"samantha": "Sam"}}
    c = dash_client({"ixl": {"Sam": {"totals": {}}}, "schoology": {}}, cfg)
    body = c.get("/api/dashboard").get_json()
    assert [r["name"] for r in body["children"]] == ["Sam"]
    assert body["children"][0]["grade"] == "5th"  # first entry's attributes
    assert all(r["name"] in body["has_data"] for r in body["children"])
    assert body["has_data"]["Sam"]["ixl"] is True


def test_canonical_roster_collapses_long_and_short_names_keeping_the_first_entry(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"children": {
        "Samantha": {"grade": "5th", "school": "A"}, "Sam": {"grade": "9th", "school": "B"}},
        "name_aliases": {"samantha": "Sam"}})
    assert state.canonical_roster() == [{"id": "samantha", "name": "Sam", "grade": "5th", "school": "A"}]
