"""Shared fixtures."""
import pytest

_PATH_VARS = (
    "SCHOOL_STATE_PATH", "SCHOOL_DB_PATH", "SCHOOL_FACTS_PATH",
    "SCHOOL_EMAIL_DIGEST", "SCHOOL_GC_PATH", "SCHOOL_SYNC_META_PATH",
    "SCHOOL_DASHBOARD_HTML", "SCHOOL_CALENDAR_PDF", "SCHOOL_DASHBOARD_CONFIG",
)


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    """Isolated state dir: only SCHOOL_STATE_DIR is set; returns its Path."""
    for var in _PATH_VARS:
        monkeypatch.delenv(var, raising=False)
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setenv("SCHOOL_STATE_DIR", str(d))
    return d
