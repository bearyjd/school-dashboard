"""timeout_for: per-source scrape timeout from <SOURCE>_SCRAPE_TIMEOUT with safe fallbacks."""
import logging

import pytest

from school_dashboard.scrape_timeout import MAX_TIMEOUT, timeout_for

VARS = ("IXL_SCRAPE_TIMEOUT", "SGY_SCRAPE_TIMEOUT", "POWERSCHOOL_SCRAPE_TIMEOUT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)


@pytest.mark.parametrize("source,default", [("ixl", 120.0), ("sgy", 900.0), ("powerschool", 900.0)])
def test_defaults(source, default, caplog):
    with caplog.at_level(logging.WARNING):
        assert timeout_for(source) == default
    assert caplog.records == []


@pytest.mark.parametrize("raw,expected", [("30", 30.0), (" 45 ", 45.0), ("1.5", 1.5), ("3600", 3600.0)])
def test_valid_values(monkeypatch, raw, expected):
    monkeypatch.setenv("SGY_SCRAPE_TIMEOUT", raw)
    assert timeout_for("sgy") == expected


@pytest.mark.parametrize("raw", ["abc", "0", "0.0", "-5", "nan", "inf", "1e3", "", "  ", "5s", ".5"])
def test_invalid_values_fall_back_to_default(monkeypatch, caplog, raw):
    monkeypatch.setenv("POWERSCHOOL_SCRAPE_TIMEOUT", raw)
    with caplog.at_level(logging.WARNING):
        assert timeout_for("powerschool") == 900.0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # an unset/blank value is "not configured" (silent); anything else warns exactly once
    assert len(warnings) == (0 if not raw.strip() else 1)


@pytest.mark.parametrize("raw", ["3601", "99999999"])
def test_huge_values_are_capped(monkeypatch, raw):
    monkeypatch.setenv("IXL_SCRAPE_TIMEOUT", raw)
    assert timeout_for("ixl") == MAX_TIMEOUT == 3600.0


def test_unknown_source_raises():
    with pytest.raises(KeyError):
        timeout_for("nope")
