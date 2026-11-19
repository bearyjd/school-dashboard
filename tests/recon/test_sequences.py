"""Ordered scrape / tap / clock-tick sequences with expected record state after every step."""
from collections.abc import Iterator
from typing import Any

import pytest

from recon.support import run_sequence, scenario_params

SEQS = scenario_params("sequences")


def _steps(s: Any) -> Iterator[tuple[dict[str, Any], int, dict[str, Any]]]:
    sc = s.values[0]
    assert isinstance(sc, dict)
    for i, step in enumerate(sc["steps"]):
        yield sc, i, step


def _check(key: str) -> list[Any]:
    return [pytest.param(sc, i, id=f"{sc['_file']}::step{i}-{step['op']}")
            for s in SEQS for sc, i, step in _steps(s) if key in step["expect"]]


def _observed(sc, i):
    return run_sequence(sc)[i][1]


@pytest.mark.parametrize("sc,i", _check("stage"))
def test_stage_after_step(sc, i):
    assert _observed(sc, i)["stage"] == sc["steps"][i]["expect"]["stage"]


@pytest.mark.parametrize("sc,i", _check("received_evidence"))
def test_received_evidence_after_step(sc, i):
    assert _observed(sc, i)["received_evidence"] == sc["steps"][i]["expect"]["received_evidence"]


@pytest.mark.parametrize("sc,i", _check("portal_status"))
def test_portal_status_after_step(sc, i):
    assert _observed(sc, i)["portal_status"] == sc["steps"][i]["expect"]["portal_status"]


@pytest.mark.parametrize("sc,i", _check("portal_conflict"))
def test_portal_conflict_after_step(sc, i):
    assert _observed(sc, i)["portal_conflict"] is sc["steps"][i]["expect"]["portal_conflict"]


@pytest.mark.parametrize("sc,i", _check("flag"))
def test_flag_after_step(sc, i):
    assert _observed(sc, i)["flag"] == sc["steps"][i]["expect"]["flag"]


@pytest.mark.parametrize("sc,i", _check("bucket"))
def test_bucket_after_step(sc, i):
    assert _observed(sc, i)["bucket"] == sc["steps"][i]["expect"]["bucket"]


@pytest.mark.parametrize("sc,i", _check("info"))
def test_info_after_step(sc, i):
    assert _observed(sc, i)["info"] == sorted(sc["steps"][i]["expect"]["info"])


@pytest.mark.parametrize("sc,i", _check("transition"))
def test_flag_transition_detected_at_step(sc, i):
    assert _observed(sc, i)["transition"] == sc["steps"][i]["expect"]["transition"]


@pytest.mark.parametrize("sc,i", _check("links"))
def test_link_state_after_step(sc, i):
    snap = _observed(sc, i)["links"]
    want = sc["steps"][i]["expect"]["links"]
    assert {k: {f: snap[k][f] for f in v} for k, v in want.items()} == want


@pytest.mark.parametrize("sc", SEQS)
def test_sequence_tap_errors_match_expectation(sc):
    got = [snap["error"] for _, snap in run_sequence(sc)]
    assert got == [step.get("expect_error") for step in sc["steps"]]
