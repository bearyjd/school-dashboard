"""CI-safe end-to-end pipeline test: synthetic scraper files -> `school-state update`
-> state file -> quick check / Flask API / morning digest prompt. No network, no creds.

All names, titles and ids here are synthetic.
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from school_dashboard import cli, digest, state
from school_dashboard.sync_meta import write_sync_source
from web.app import app as flask_app

FIXTURE = Path(__file__).parent.parent / "fixtures" / "powerschool-summary.json"
TODAY = date.today()


def _fresh_ps_scrape() -> dict:
    """Fixture with a current scraped_at and dates relative to today (so nothing goes stale)."""
    data = json.loads(FIXTURE.read_text())
    data["scraped_at"] = datetime.now().isoformat(timespec="seconds")
    for child in data["children"]:
        for key, offset in (("missing", -4), ("late", -2), ("upcoming", 2)):
            for item in child[key]:
                item["due"] = (TODAY + timedelta(days=offset)).isoformat()
    return data


@pytest.fixture
def pipeline(state_env, tmp_path, monkeypatch):
    cfg = tmp_path / "roster.json"
    cfg.write_text(json.dumps({"children": {
        "Alex": {"grade": "7th", "school": "Test Middle"},
        "Jack": {"grade": "5th", "school": "Test Elementary"},
        "Jo": {"grade": "3rd", "school": "Test Elementary"},
    }}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    state._config_cache = None
    inputs = tmp_path / "inputs"
    (inputs / "ixl").mkdir(parents=True)
    (inputs / "ixl" / "alex-assigned.json").write_text(json.dumps(
        {"totals": {"Math": {"assigned": 4, "done": 1, "remaining": 3}}, "remaining": []}))
    (inputs / "sgy.json").write_text(json.dumps({"per_child": [{
        "child": {"name": "Alex Tester"},
        "assignments": [{"title": "Synthetic Worksheet", "course": "Art",
                         "due_date": TODAY.isoformat(), "status": "open"}],
        "grades": [], "announcements": []}]}))
    ps = inputs / "ps.json"
    ps.write_text(json.dumps(_fresh_ps_scrape()))
    yield {"dir": state_env, "inputs": inputs, "ps": ps}
    state._config_cache = None


def _run_update(p) -> Path:
    argv = ["school-state", "update", "--ixl-dir", str(p["inputs"] / "ixl"),
            "--sgy-file", str(p["inputs"] / "sgy.json"), "--ps-file", str(p["ps"])]
    with patch.object(sys, "argv", argv):
        cli.main()
    return p["dir"] / "school-state.json"


def test_pipeline_end_to_end(pipeline):
    state_file = _run_update(pipeline)
    s = json.loads(state_file.read_text())
    assert {k: len(v["missing"]) for k, v in s["powerschool"].items()} == {"Alex": 1, "Jack": 0, "Jo": 0}
    assert s["powerschool_meta"]["warnings"] == []

    text, _ = digest.build_quick_check(str(state_file), today=TODAY.isoformat())
    assert "Alex: IXL 3 remaining (Math)" in text
    assert "PS: missing 1" in text and "late 1" in text

    write_sync_source("ps", "ok")
    client = flask_app.test_client()
    dash = client.get("/api/dashboard")
    assert dash.status_code == 200
    body = dash.get_json()
    counts = {k: (len(v["missing"]), len(v["late"]), len(v["upcoming"])) for k, v in body["powerschool"].items()}
    assert counts == {"Alex": (1, 1, 1), "Jack": (0, 0, 1), "Jo": (0, 0, 0)}
    assert body["has_data"]["Alex"] == {"ixl": True, "sgy": True, "ps": True}
    assert [c["name"] for c in body["children"]] == ["Alex", "Jack", "Jo"]

    assert client.get("/api/readiness").status_code == 200
    meta = client.get("/api/sync/meta")
    assert meta.status_code == 200 and meta.get_json()["ps"]["last_result"] == "ok"


def test_morning_digest_prompt_carries_powerschool_block(pipeline, tmp_path):
    state_file = _run_update(pipeline)
    resp = MagicMock(ok=True, status_code=200, headers={"content-type": "application/json"})
    resp.text = json.dumps({"choices": [{"message": {"content": "Good morning"}}]})
    resp.json.return_value = json.loads(resp.text)
    with patch("school_dashboard.llm.requests.post", return_value=resp) as post:
        text, _cards = digest.build_morning_digest(
            str(state_file), str(tmp_path / "school.db"), str(tmp_path / "facts.json"), [],
            "http://llm.invalid", "k", "m", today=TODAY.isoformat())
    assert text == "Good morning"
    prompt = post.call_args.kwargs["json"]["messages"][0]["content"]
    assert "Missing / late / due soon (PowerSchool" in prompt
    assert "MISSING (1)" in prompt and "LATE (1)" in prompt and "DUE SOON (1)" in prompt
    assert "Quiz 4 Corrections" in prompt

    with patch("school_dashboard.digest.requests.post", return_value=MagicMock(ok=True)) as ntfy:
        digest.send_ntfy("topic-x", text, title="Morning")
    assert ntfy.call_args.args[0] == "https://ntfy.sh/topic-x"


def test_bad_scrape_keeps_previous_missing_items(pipeline):
    state_file = _run_update(pipeline)
    bad = _fresh_ps_scrape()
    bad["warnings"] = ["login failed"]
    for child in bad["children"]:
        child["missing"] = []
    pipeline["ps"].write_text(json.dumps(bad))
    _run_update(pipeline)

    s = json.loads(state_file.read_text())
    assert len(s["powerschool"]["Alex"]["missing"]) == 1
    assert "not applied" in s["powerschool_meta"]["warnings"][0]

    body = flask_app.test_client().get("/api/dashboard").get_json()
    assert len(body["powerschool"]["Alex"]["missing"]) == 1
    assert any("not applied" in w for w in body["powerschool_warnings"])
    text, _ = digest.build_quick_check(str(state_file), today=TODAY.isoformat())
    assert "missing 1" in text
