"""GameChanger: games are events, not assignments, so this adapter yields nothing."""
from __future__ import annotations

from typing import Any, Callable

from .base import AdapterResult, empty_result


class GameChangerAdapter:
    source = "gamechanger"

    def parse(self, payload: Any, *, resolve_kid: Callable[[str], str]) -> AdapterResult:
        if not isinstance(payload, dict):
            return empty_result(self.source, "payload is not a GameChanger summary")
        return AdapterResult(self.source, (), frozenset(), frozenset(), ())
