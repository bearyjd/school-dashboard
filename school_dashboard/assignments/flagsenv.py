"""One reader for the recon feature flags (RECON_INGEST_ENABLED, RECON_PUSH_ENABLED)."""
from __future__ import annotations

import os

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def enabled(name: str) -> bool:
    """True only for an explicit 1/true/yes/on (case-insensitive); unset, falsy or unknown is off."""
    return os.environ.get(name, "").strip().casefold() in _TRUTHY
