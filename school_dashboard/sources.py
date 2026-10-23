"""Per-source scrape toggles: SOURCE_<NAME>_ENABLED env vars (default enabled)."""
import os

_FALSY = frozenset({"0", "false", "no", "off"})


def source_enabled(name: str) -> bool:
    """False only when SOURCE_<NAME>_ENABLED is set to 0/false/no/off (case-insensitive)."""
    raw = os.environ.get(f"SOURCE_{name.upper()}_ENABLED", "")
    return raw.strip().lower() not in _FALSY
