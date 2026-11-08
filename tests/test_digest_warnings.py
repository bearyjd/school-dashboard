"""DATA WARNINGS in the LLM digest prompts: absent (byte-identical) when healthy, present when not."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from digest_prompt_support import HEALTHY_STATE, capture_all
from school_dashboard import digest, scrape_meta

GOLDEN = Path(__file__).parent / "fixtures" / "digest_prompts"
BUILDERS = ("morning", "afternoon", "night", "friday", "sunday")
NOW = datetime(2026, 4, 10, 12, tzinfo=timezone.utc)
INSTRUCTION = (
    "If DATA WARNINGS lists an unavailable source, say so in one short line and do not "
    "present that source's items as complete or as 'all done'."
)


def _degraded() -> dict:
    state = copy.deepcopy(HEALTHY_STATE)
    state["scrape_meta"] = {"ixl": {"unavailable": {"Alpha": {"reason": "failed", "since": "2026-04-08T12:00:00+00:00"}}}}
    return state


def _prompt(messages: list) -> str:
    return "\n".join(m["content"] for m in messages)


@pytest.mark.parametrize("name", BUILDERS)
def test_healthy_state_prompt_is_byte_identical_to_golden(tmp_path, name):
    got = capture_all(tmp_path, HEALTHY_STATE)[name]
    assert got == json.loads((GOLDEN / f"{name}.json").read_text())


@pytest.mark.parametrize("name", BUILDERS)
def test_empty_scrape_meta_adds_nothing(tmp_path, name):
    state = {**HEALTHY_STATE, "scrape_meta": {"ixl": {"unavailable": {}}, "schoology": {"unavailable": {}}}}
    got = capture_all(tmp_path, state)[name]
    assert got == json.loads((GOLDEN / f"{name}.json").read_text())


@pytest.mark.parametrize("name", BUILDERS)
def test_warning_block_and_instruction_in_every_builder(tmp_path, name):
    prompt = _prompt(capture_all(tmp_path, _degraded())[name])
    assert "DATA WARNINGS:\n- IXL Alpha: the last scrape failed; showing the last good data from 2026-04-09" in prompt
    assert prompt.count(INSTRUCTION) == 1
    assert prompt.count("DATA WARNINGS:") == 1


def test_block_helper_is_empty_without_warnings():
    assert scrape_meta.data_warnings_block(HEALTHY_STATE, NOW) == ""


def test_block_helper_exact_text():
    assert scrape_meta.data_warnings_block(_degraded(), NOW) == (
        "DATA WARNINGS:\n"
        "- IXL Alpha: the last scrape failed; showing the last good data from 2026-04-09 (unavailable for 2 days)\n"
        + INSTRUCTION
    )


def test_quick_check_unchanged_by_helper(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_degraded()))
    text, _ = digest.build_quick_check(str(p), today="2026-04-10")
    assert "DATA WARNINGS" not in text
    assert "IXL unavailable" in text
