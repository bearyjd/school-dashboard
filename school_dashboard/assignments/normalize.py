"""Title and class key normalisation."""
from __future__ import annotations

import re
import unicodedata
from types import MappingProxyType
from typing import Mapping

_NO_ALIASES: Mapping[str, str] = MappingProxyType({})
_SPACES = re.compile(r"\s+")


def _norm(text: str | None) -> str:
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    kept = "".join(ch for ch in folded if not unicodedata.category(ch).startswith("P"))
    return _SPACES.sub(" ", kept).strip()


def title_key(title: str) -> str:
    """NFKC, casefold, punctuation removed, whitespace collapsed."""
    return _norm(title)


def class_key(name: str | None, aliases: Mapping[str, str] = _NO_ALIASES) -> str:
    """Normalised class name, resolved through aliases (compared normalised)."""
    key = _norm(name)
    if not key:
        return ""
    for alias, target in aliases.items():
        if _norm(alias) == key:
            return _norm(target)
    return key
