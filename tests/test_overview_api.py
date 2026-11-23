import json
from datetime import date, datetime, timedelta, timezone

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


def test_freshness_follows_watchdog_active_sources_and_first_seen(client, state_env, monkeypatch):
    monkeypatch.setenv("SOURCE_IXL_ENABLED", "0")
    monkeypatch.delenv("GC_TOKEN", raising=False)
    monkeypatch.delenv("GC_EMAIL", raising=False)
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
    (state_env / "watchdog.json").write_text(json.dumps({"first_seen": {"sgy": old}}))
    fr = client.get("/api/overview").get_json()["freshness"]
    assert fr["ixl"]["paused"] is True and fr["gc"]["paused"] is True
    assert fr["sgy"]["paused"] is False and fr["sgy"]["issue"] == "never_ok"


@pytest.mark.parametrize(
    "portal,base,scraper_env,legacy,origin",
    [
        ("https://explicit.example.invalid", "https://instance.example.invalid",
         "https://dotenv.example.invalid", "https://legacy.example.invalid", "https://explicit.example.invalid"),
        (None, "https://instance.example.invalid",
         "https://dotenv.example.invalid", "https://legacy.example.invalid", "https://instance.example.invalid"),
        (None, "https://api.schoology.com",
         "https://dotenv.example.invalid", "https://legacy.example.invalid", "https://dotenv.example.invalid"),
        (None, None, None, "https://legacy.example.invalid", "https://legacy.example.invalid"),
        (None, "https://api.schoology.com", None, None, ""),
    ],
)
def test_schoology_relative_link_uses_configured_portal(
    client, state_env, tmp_path, monkeypatch, portal, base, scraper_env, legacy, origin,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name, value in (("SGY_PORTAL_URL", portal), ("SGY_BASE_URL", base)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    scraper = tmp_path / ".sgy"
    scraper.mkdir()
    if scraper_env:
        (scraper / ".env").write_text(f"SGY_BASE_URL={scraper_env}\n")
    if legacy:
        (scraper / "config.json").write_text(json.dumps({"base_url": legacy}))

    link = "/assignment/1001"
    (state_env / "school-state.json").write_text(json.dumps({"schoology": {"Alpha": {
        "assignments": [{"title": "Sample worksheet", "due_date": date.today().isoformat(), "link": link}]
    }}}))
    expected = origin + link if origin else ""
    dashboard = client.get("/api/dashboard").get_json()
    overview = client.get("/api/overview").get_json()
    assert dashboard["schoology"]["Alpha"][0]["url"] == expected
    assert overview["items"][0]["url"] == expected
