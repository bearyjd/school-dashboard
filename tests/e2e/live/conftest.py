"""Redact failure output for live tests: scraped data is real family data.

Only messages raised via pytest.fail/skip (written count-only by the tests) are kept.
Any other failure (assertion introspection, KeyError, ...) is reduced to its type name,
and captured stdout/stderr/log sections are dropped.
"""
import pytest

_KEEP = (pytest.fail.Exception, pytest.skip.Exception)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not report.failed or item.get_closest_marker("live") is None:
        return
    exc = call.excinfo.value if call.excinfo else None
    if isinstance(exc, _KEEP):
        detail = str(exc)
    else:
        detail = f"unexpected {type(exc).__name__ if exc else 'failure'} (details redacted)"
    report.longrepr = f"live test failed in {report.when}: {detail}"
    report.sections = []
