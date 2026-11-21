"""Helper for sync worker tests that mock `subprocess.run`: route scraper calls to the same mock."""
from typing import Any
from unittest.mock import MagicMock, patch

from school_dashboard.scrape_run import ScrapeRun


def route_scrapers(mock_run: MagicMock):
    """Patch web.app.run_scraper so scraper invocations hit `mock_run` like they did before
    scrapers moved off subprocess.run (same call order, same exceptions, same results)."""

    def adapter(cmd: list[str], *, timeout: float, env: Any = None) -> ScrapeRun:
        res = mock_run(cmd, timeout=timeout, env=env)
        return ScrapeRun(
            returncode=getattr(res, "returncode", 0), stdout=getattr(res, "stdout", ""),
            stderr=getattr(res, "stderr", ""), timed_out=False,
        )

    return patch("web.app.run_scraper", side_effect=adapter)
