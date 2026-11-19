import pytest

from school_dashboard.assignments import flagsenv


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_truthy_values_enable(monkeypatch, value):
    monkeypatch.setenv("RECON_INGEST_ENABLED", value)
    assert flagsenv.enabled("RECON_INGEST_ENABLED") is True


@pytest.mark.parametrize("value", ["", "0", "false", "No", "OFF", "  ", "banana"])
def test_falsy_or_unknown_values_disable(monkeypatch, value):
    monkeypatch.setenv("RECON_PUSH_ENABLED", value)
    assert flagsenv.enabled("RECON_PUSH_ENABLED") is False


def test_unset_is_off(monkeypatch):
    monkeypatch.delenv("RECON_INGEST_ENABLED", raising=False)
    assert flagsenv.enabled("RECON_INGEST_ENABLED") is False


def test_each_flag_is_independent(monkeypatch):
    monkeypatch.setenv("RECON_INGEST_ENABLED", "1")
    monkeypatch.delenv("RECON_PUSH_ENABLED", raising=False)
    assert flagsenv.enabled("RECON_INGEST_ENABLED") and not flagsenv.enabled("RECON_PUSH_ENABLED")
