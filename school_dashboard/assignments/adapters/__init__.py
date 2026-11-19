"""Source adapters: raw scrape payloads -> SourceRecords (pure parsers)."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from .base import Adapter, AdapterResult
from .gamechanger import GameChangerAdapter
from .ixl import IxlAdapter
from .powerschool import PowerSchoolAdapter
from .schoology import SchoologyAdapter

ADAPTERS: Mapping[str, Adapter] = MappingProxyType({
    "schoology": SchoologyAdapter(),
    "powerschool": PowerSchoolAdapter(),
    "ixl": IxlAdapter(),
    "gamechanger": GameChangerAdapter(),
})

__all__ = ["ADAPTERS", "Adapter", "AdapterResult"]
