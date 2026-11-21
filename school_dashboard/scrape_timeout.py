"""Per-source scraper timeout from `<SOURCE>_SCRAPE_TIMEOUT` (seconds), read at call time.

Mirrors `scrape_timeout` in sync/school-sync.sh: a value that is not a positive plain
number logs one warning and falls back to the default; values above MAX_TIMEOUT are capped.
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

DEFAULTS = {"ixl": 120.0, "sgy": 900.0, "powerschool": 900.0}
MAX_TIMEOUT = 3600.0
_NUMBER = re.compile(r"\d+(\.\d+)?")


def timeout_for(source: str) -> float:
    """Timeout in seconds for `source` (KeyError for an unknown source)."""
    default = DEFAULTS[source]
    var = f"{source.upper()}_SCRAPE_TIMEOUT"
    raw = os.environ.get(var, "").strip()
    if not raw:
        return default
    value = float(raw) if _NUMBER.fullmatch(raw) else 0.0
    if value <= 0:
        logger.warning("%s=%r is not a positive number of seconds; using %g", var, raw, default)
        return default
    return min(value, MAX_TIMEOUT)
