"""Class aliases: `class_settings.aliases` is a JSON list of alias names on the canonical class row."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from . import repo
from .normalize import class_key


class AliasError(ValueError):
    """The alias request is invalid (unknown kid, empty or identical names)."""


@dataclass(frozen=True)
class AliasRow:
    kid: str
    class_key: str
    alias: str


def _names(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [n for n in data if isinstance(n, str)] if isinstance(data, list) else []


def _require_migrated(conn: sqlite3.Connection) -> None:
    try:
        repo.require_migrated(conn)
    except sqlite3.OperationalError as exc:  # the recon tables are not there at all
        raise repo.NotMigratedError("legacy items are not migrated; run: school-state assignments migrate") from exc


def add(conn: sqlite3.Connection, *, kid: str, from_name: str, to_name: str) -> AliasRow:
    """Make `from_name` an alias of the class `to_name` for `kid`; idempotent, one transaction."""
    alias, canonical = from_name.strip(), class_key(to_name)
    if not alias or not canonical or not class_key(alias):
        raise AliasError("both --from and --to need a non-empty class name")
    if class_key(alias) == canonical:
        raise AliasError("--from and --to name the same class")
    _require_migrated(conn)
    if conn.execute("SELECT 1 FROM assignments WHERE kid = ? LIMIT 1", (kid,)).fetchone() is None:
        raise AliasError("unknown kid: no assignments for that name")
    with repo.transaction(conn):
        row = conn.execute("SELECT aliases FROM class_settings WHERE kid = ? AND class_key = ?",
                           (kid, canonical)).fetchone()
        names = _names(row["aliases"]) if row else []
        if all(class_key(n) != class_key(alias) for n in names):
            names = [*names, alias]
        conn.execute(
            "INSERT INTO class_settings (kid, class_key, aliases) VALUES (?, ?, ?)"
            " ON CONFLICT (kid, class_key) DO UPDATE SET aliases = excluded.aliases",
            (kid, canonical, json.dumps(names)))
    return AliasRow(kid, canonical, alias)


def list_aliases(conn: sqlite3.Connection, kid: str | None = None) -> list[AliasRow]:
    _require_migrated(conn)
    rows = conn.execute("SELECT kid, class_key, aliases FROM class_settings WHERE (? IS NULL OR kid = ?)"
                        " ORDER BY kid, class_key", (kid, kid)).fetchall()
    return [AliasRow(r["kid"], r["class_key"], n) for r in rows for n in _names(r["aliases"])]
