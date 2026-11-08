"""Scrape warnings recorded by `school-state update` reach /api/dashboard and /api/overview."""
import json

import pytest

from school_dashboard import state
from web.app import app as flask_app

META = {"ixl": {"unavailable": {"Alex": "totals incomplete"},
                "warnings": ["IXL Alex: totals incomplete; previous data kept"]},
        "schoology": {"unavailable": {}, "warnings": []}}


@pytest.fixture(autouse=True)
def reset(tmp_path, monkeypatch):
    state._config_cache = None
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "none.json"))
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps({"scrape_meta": META}))
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(sp))
    yield
    state._config_cache = None


def test_dashboard_exposes_scrape_warnings():
    with flask_app.test_client() as c:
        body = c.get("/api/dashboard").get_json()
    assert body["scrape_warnings"] == ["IXL Alex: totals incomplete; previous data kept"]


def test_overview_warnings_include_scrape_warnings():
    with flask_app.test_client() as c:
        body = c.get("/api/overview").get_json()
    assert "IXL Alex: totals incomplete; previous data kept" in body["warnings"]


def test_scrape_warnings_tolerates_garbage_meta():
    assert state.scrape_warnings({"scrape_meta": "x"}) == []
    assert state.scrape_warnings({"scrape_meta": {"ixl": {"warnings": None}, "sgy": 3}}) == []
