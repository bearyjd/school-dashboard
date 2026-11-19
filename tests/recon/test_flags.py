"""flags.py: compute_flag over the per-flag scenario fixtures, plus bucket_counts."""
import pytest

from recon.support import compute_scenario_flag, scenario_params
from school_dashboard.assignments import flags
from school_dashboard.assignments.model import FlagResult


@pytest.mark.parametrize("sc", scenario_params("flags", expect_key="flag"))
def test_flag_matches_scenario(sc):
    assert compute_scenario_flag(sc).flag == sc["expect"]["flag"]


@pytest.mark.parametrize("sc", scenario_params("flags", expect_key="bucket"))
def test_bucket_matches_scenario(sc):
    assert compute_scenario_flag(sc).bucket == sc["expect"]["bucket"]


@pytest.mark.parametrize("sc", scenario_params("flags", expect_key="info"))
def test_info_chips_match_scenario(sc):
    assert sorted(compute_scenario_flag(sc).info) == sorted(sc["expect"]["info"])


@pytest.mark.parametrize("sc", scenario_params("flags", expect_key="unverified"))
def test_unverified_overlay_matches_scenario(sc):
    assert compute_scenario_flag(sc).unverified is sc["expect"]["unverified"]


@pytest.mark.parametrize("sc", scenario_params("flags", expect_key="unverified_sources"))
def test_unverified_sources_are_sorted_and_match_scenario(sc):
    assert tuple(compute_scenario_flag(sc).unverified_sources) == tuple(sc["expect"]["unverified_sources"])


@pytest.mark.parametrize("sc", scenario_params("flags", expect_key="flag_not_in"))
def test_exempt_kinds_never_get_reconciliation_flags(sc):
    assert compute_scenario_flag(sc).flag not in sc["expect"]["flag_not_in"]


def R(bucket, flag="x"):
    return FlagResult(flag=flag, bucket=bucket, unverified=False, unverified_sources=(), info=())


def test_bucket_counts_groups_by_bucket():
    results = [R("needs_action"), R("needs_action"), R("do_tonight"), R("waiting_on_teacher")]
    counts = flags.bucket_counts(results)
    assert (counts["needs_action"], counts["do_tonight"], counts["waiting_on_teacher"]) == (2, 1, 1)


def test_bucket_counts_ignores_results_without_bucket():
    counts = flags.bucket_counts([R(None, "upcoming"), R(None, None)])
    assert sum(counts.values()) == 0


def test_bucket_counts_accepts_a_generator():
    assert flags.bucket_counts(R("do_tonight") for _ in range(3))["do_tonight"] == 3
