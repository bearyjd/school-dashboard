"""/api/chat keeps the DATA WARNINGS even when the state JSON is cut at 8000 chars."""
import json

import pytest

from web import app as app_module

BIG = {"schoology": {"Alpha": {"assignments": [
    {"title": f"Assignment {i} " + "x" * 40, "due_date": "2026-04-10", "course": "Math"} for i in range(400)]}}}
META = {"ixl": {"unavailable": {"Alpha": {"reason": "failed", "since": "2026-04-08T12:00:00+00:00"}}}}


@pytest.fixture
def prompt_for(tmp_path, monkeypatch):
    def run(state):
        sp = tmp_path / "state.json"
        sp.write_text(json.dumps(state))
        monkeypatch.setenv("SCHOOL_STATE_PATH", str(sp))
        monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(tmp_path / "m.json"))
        return app_module.build_system_prompt()
    return run


def test_warnings_survive_truncation_and_sit_above_the_state(prompt_for):
    prompt = prompt_for({**BIG, "scrape_meta": META})
    assert "x" * 40 in prompt and len(json.dumps(BIG, indent=2)) > 8000
    assert "- IXL Alpha: the last scrape failed" in prompt
    assert prompt.index("DATA WARNINGS") < prompt.index("=== SCHOOL STATE")
    assert '"scrape_meta"' not in prompt


def test_no_warnings_adds_no_line_and_hides_empty_scrape_meta(prompt_for):
    healthy = prompt_for(BIG)
    empty = prompt_for({**BIG, "scrape_meta": {"ixl": {"unavailable": {}}}})
    assert "DATA WARNINGS" not in healthy and "DATA WARNINGS" not in empty
    assert empty == healthy
