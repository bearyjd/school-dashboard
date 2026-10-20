"""Single per-call resolver for state file locations.

Every function reads the environment on each call, so tests and runtime
overrides take effect without re-importing modules.
"""
import os
from pathlib import Path

DEFAULT_STATE_DIR = "/app/state"


def state_dir() -> Path:
    """SCHOOL_STATE_DIR, else the parent of SCHOOL_STATE_PATH, else the default."""
    explicit = os.environ.get("SCHOOL_STATE_DIR")
    if explicit:
        return Path(explicit)
    state_file = os.environ.get("SCHOOL_STATE_PATH")
    if state_file:
        return Path(state_file).parent
    return Path(DEFAULT_STATE_DIR)


def _resolve(env_var: str, filename: str) -> Path:
    override = os.environ.get(env_var)
    return Path(override) if override else state_dir() / filename


def state_path() -> Path:
    return _resolve("SCHOOL_STATE_PATH", "school-state.json")


def db_path() -> Path:
    return _resolve("SCHOOL_DB_PATH", "school.db")


def facts_path() -> Path:
    return _resolve("SCHOOL_FACTS_PATH", "facts.json")


def email_digest_path() -> Path:
    return _resolve("SCHOOL_EMAIL_DIGEST", "email-digest.json")


def gc_path() -> Path:
    return _resolve("SCHOOL_GC_PATH", "gc-schedule.json")


def sync_meta_path() -> Path:
    return _resolve("SCHOOL_SYNC_META_PATH", "sync_meta.json")


def dashboard_html_path() -> Path:
    return _resolve("SCHOOL_DASHBOARD_HTML", "school-dashboard.html")


def calendar_pdf_path() -> Path:
    return _resolve("SCHOOL_CALENDAR_PDF", "calendar.pdf")
