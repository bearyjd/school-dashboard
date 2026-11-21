"""Result types of the ingest engine (frozen)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KidResult:
    kid: str
    ok: bool
    created: int = 0
    linked: int = 0
    merged: int = 0
    ambiguous: int = 0
    absent: int = 0
    stage_changes: int = 0
    flag_changes: int = 0
    pushes_enqueued: int = 0
    pushes_debounced: int = 0
    error: str | None = None


@dataclass(frozen=True)
class IngestReport:
    ok: bool
    kids: tuple[KidResult, ...]
    warnings: tuple[str, ...]
    baseline: bool
    data_as_of: str
