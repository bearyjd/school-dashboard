"""Scrape warnings recorded by `school-state update` reach /api/dashboard and /api/overview."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from school_dashboard import scrape_meta, state
from web.app import app as flask_app

SINCE = (datetime.now(timezone.utc) - timedelta(days=2, hours=1)).isoformat()
META = {"ixl": {"unavailable": {"Alex": {"reason": "failed", "since": SINCE}}},
        "schoology": {"unavailable": {}}}
WARNING = "IXL Alex: the last scrape failed; no earlier data to show"


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
    assert body["scrape_warnings"] == [WARNING + " (unavailable for 2 days)"]


def test_overview_warnings_include_scrape_warnings():
    with flask_app.test_client() as c:
        body = c.get("/api/overview").get_json()
    assert WARNING + " (unavailable for 2 days)" in body["warnings"]


def test_scrape_warnings_tolerates_garbage_meta():
    assert scrape_meta.scrape_warnings({"scrape_meta": "x"}) == []
    assert scrape_meta.scrape_warnings({"scrape_meta": {"ixl": {"unavailable": None}, "schoology": 3}}) == []
    assert scrape_meta.scrape_warnings({"scrape_meta": {"ixl": {"unavailable": {"A": "bad"}}}}) == []
