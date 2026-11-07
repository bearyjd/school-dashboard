import json
from datetime import date, timedelta

import pytest

from school_dashboard import state
from web.app import app as flask_app

KEYS = {"generated_at", "kids", "items", "events", "freshness", "warnings"}


@pytest.fixture
def client(state_env, tmp_path, monkeypatch):
    cfg = tmp_path / "roster.json"
    cfg.write_text(json.dumps({"children": {"Alpha": {"grade": "1st", "school": "S"}}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    state._config_cache = None
    yield flask_app.test_client()
    state._config_cache = None


def test_200_and_json_keys(client, state_env):
    today = date.today()
    (state_env / "school-state.json").write_text(json.dumps({"powerschool": {"Alpha": {
        "missing": [{"title": "Quiz", "due": (today - timedelta(days=3)).isoformat()}]}}}))
    (state_env / "gc-schedule.json").write_text(json.dumps({"teams": [{
        "team_name": "Lions", "child": "Alpha", "schedule": [
            {"date": (today + timedelta(days=1)).isoformat(), "time": "09:00"},
            {"date": (today + timedelta(days=10)).isoformat(), "time": "09:00"}]}]}))
    resp = client.get("/api/overview")
    assert resp.status_code == 200
    body = resp.get_json()
    assert KEYS <= set(body)
    assert [i["status"] for i in body["items"]] == ["missing"]
    assert len(body["events"]) == 2
    narrow = client.get("/api/overview?days=3&bogus=1").get_json()
    assert len(narrow["events"]) == 1


def test_empty_state_returns_empty_lists(client):
    resp = client.get("/api/overview")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["items"] == [] and body["events"] == []
    assert body["warnings"]
