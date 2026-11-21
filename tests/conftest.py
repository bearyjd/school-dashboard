"""Shared fixtures."""
import os
import shutil
import subprocess

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


_REAL_SCRAPERS = frozenset({"sgy", "ixl", "powerschool", "gc"})
_SCRAPER_SCRIPTS = {
    "gc-scrape.sh": ("gc",), "ixl-cron.sh": ("ixl",),
    "school-sync.sh": ("sgy", "ixl", "powerschool", "gc"),
}
_REAL_POPEN = subprocess.Popen


def _scraper_name(argv: list[str]) -> str | None:
    """The real-scraper CLI or wrapper-script name this argv would start, if any."""
    if not argv:
        return None
    head = os.path.basename(argv[0])
    if head in _REAL_SCRAPERS:
        return head
    for arg in argv[:2]:
        if os.path.basename(arg) in _SCRAPER_SCRIPTS:
            return os.path.basename(arg)
    return None


def _clis_for(name: str) -> tuple[str, ...]:
    return _SCRAPER_SCRIPTS.get(name, (name,))


def _is_sandboxed(cli: str, argv0: str, env, allowed: tuple[str, ...], strict: bool) -> bool:
    """True when `cli` resolves, via the PATH the child would get, to a file inside `allowed`.
    A name that resolves nowhere is fine for a wrapper script (it cannot start anything) but
    not for a direct start (tests must route or fake it)."""
    path = (env if env is not None else os.environ).get("PATH")
    found = argv0 if os.sep in argv0 and cli == os.path.basename(argv0) else shutil.which(cli, path=path)
    if found is None:
        return not strict
    return any(os.path.realpath(found).startswith(a) for a in allowed)


def _make_guarded_popen(allowed: tuple[str, ...]):
    class GuardedPopen(_REAL_POPEN):  # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **kw):
            argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
            argv = [os.fsdecode(x) for x in argv]
            name = _scraper_name(argv)
            if name and not all(
                _is_sandboxed(c, argv[0], kw.get("env"), allowed, strict=name in _REAL_SCRAPERS)
                for c in _clis_for(name)
            ):
                raise AssertionError(f"test tried to start a real scraper: {name}")
            super().__init__(args, *a, **kw)

    return GuardedPopen


@pytest.fixture(autouse=True)
def _no_real_scrapers(tmp_path_factory, monkeypatch):
    """No test may start a real scraper CLI: a Popen whose argv[0] is sgy/ixl/powerschool/gc
    (or gc-scrape.sh/ixl-cron.sh) must resolve into the pytest tmp dir (a fake the test made).
    Patching subprocess.Popen covers subprocess.run, scrape_run.run_scraper and web.app alike;
    tests that patch run_scraper/subprocess.run (route_scrapers) never reach Popen."""
    base = os.path.realpath(str(tmp_path_factory.getbasetemp()))
    monkeypatch.setattr(subprocess, "Popen", _make_guarded_popen((base,)))
