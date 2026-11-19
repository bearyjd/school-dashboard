"""Multi-run ingest scenarios through the REAL adapters, ingest and SQLite (fixtures: fixtures/recon/ingest)."""
import pytest

from recon.ingest_support import drive
from recon.support import scenario_params


@pytest.mark.parametrize("sc", scenario_params("ingest"))
def test_ingest_scenario(sc, tmp_path):
    drive(tmp_path, sc)
