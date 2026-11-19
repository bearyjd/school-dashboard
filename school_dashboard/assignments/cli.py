"""`school-state assignments migrate|export` argparse wiring and exit codes."""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from typing import Any

from school_dashboard import paths, sync_meta, watchdog
from school_dashboard.assignments import aliases, migrate, repo, report, schema
from school_dashboard.assignments.adapters.base import clean_text

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_FAILED, EXIT_INPUT, EXIT_REFUSED = 0, 1, 2, 3


def _out(text: str) -> None:
    sys.stdout.write(text)


def _render_text(rep: migrate.MigrationReport) -> str:
    mode = "dry run, nothing written" if rep.dry_run else "complete"
    lines = [f"Assignments migration ({mode})",
             f"  rows in: {rep.rows_in}, created: {rep.created}, merged: {len(rep.merged)}, "
             f"already migrated: {rep.already_migrated}"]
    lines += [f"  warning: {w}" for w in rep.warnings]
    if rep.backup_dir:
        lines.append(f"  backup: {rep.backup_dir}")
    return "\n".join(lines) + "\n"


def _migrate(args: argparse.Namespace) -> int:
    state_file = args.state_file or str(paths.state_path())
    rep = migrate.run(str(paths.db_path()), state_file, dry_run=args.dry_run,
                      now=datetime.now().astimezone())
    _out(json.dumps(dataclasses.asdict(rep), indent=2, default=str) + "\n" if args.json else _render_text(rep))
    return EXIT_OK


def _export(args: argparse.Namespace) -> int:
    data: Any = migrate.export(str(paths.db_path()))
    _out(json.dumps(data, indent=2, default=str) + "\n")
    return EXIT_OK


def _existing_db(readonly: bool = False) -> sqlite3.Connection:
    db = str(paths.db_path())
    if not Path(db).exists():
        raise repo.NotMigratedError("no database yet; run: school-state assignments migrate")
    return schema.connect(db, readonly=readonly)


def _report(args: argparse.Namespace) -> int:
    rep = report.run(str(paths.db_path()), now=datetime.now(watchdog.get_tz()), meta=sync_meta.read_sync_meta())
    shown = report.redact(rep) if args.redact else rep
    _out(json.dumps(shown, indent=2, default=str) + "\n" if args.json else report.render_text(shown))
    sys.stderr.write(f"report: {len(rep['kids'])} kids, {len(rep['unmatched_pairs'])} unmatched pairs, "
                     f"{len(rep['alias_suggestions'])} alias suggestions\n")
    return EXIT_OK


def _alias(args: argparse.Namespace) -> int:
    conn = _existing_db(readonly=args.alias_command == "list")
    try:
        if args.alias_command == "add":
            row = aliases.add(conn, kid=args.kid, from_name=args.from_name, to_name=args.to_name)
            _out(f"{row.kid}: {row.alias} -> {row.class_key}\n")
            return EXIT_OK
        rows = [dataclasses.asdict(r) | {"class": r.class_key} for r in aliases.list_aliases(conn, args.kid)]
        _out(json.dumps(rows, indent=2) + "\n" if args.json else
             "".join(f"{clean_text(r['kid'], 120)}: {clean_text(r['alias'], 120)} -> {clean_text(r['class'], 120)}\n"
                    for r in rows))
        return EXIT_OK
    finally:
        conn.close()


def cmd_assignments(args: argparse.Namespace) -> None:
    handlers = {"migrate": _migrate, "export": _export, "report": _report, "alias": _alias}
    handler = handlers.get(getattr(args, "assignments_command", None) or "")
    if handler is None:
        sys.stderr.write("usage: school-state assignments {migrate|export|report|alias} [--json]\n")
        sys.exit(EXIT_FAILED)
    try:
        code = handler(args)
    except (repo.NotMigratedError, migrate.MigrationRefused) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        code = EXIT_REFUSED
    except (aliases.AliasError, migrate.MigrationInputError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        code = EXIT_INPUT
    except migrate.MigrationError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        code = EXIT_FAILED
    except (sqlite3.Error, repo.BusyError) as exc:
        sys.stderr.write(f"Error: database failure ({type(exc).__name__})\n")
        code = EXIT_FAILED
    except OSError as exc:
        sys.stderr.write(f"Error: file system failure ({type(exc).__name__})\n")
        code = EXIT_FAILED
    sys.exit(code)


def add_parser(subs: Any) -> argparse.ArgumentParser:
    p = subs.add_parser("assignments", help="Reconciliation v2 assignments: migrate legacy items, export")
    ss = p.add_subparsers(dest="assignments_command")
    m = ss.add_parser("migrate", help="Copy legacy items and action items into assignments")
    m.add_argument("--dry-run", action="store_true", help="Read-only report; works without maintenance mode")
    m.add_argument("--json", action="store_true", help="Machine-readable report on stdout")
    m.set_defaults(func=cmd_assignments)
    e = ss.add_parser("export", help="Dump assignments, links, events and meta as JSON")
    e.add_argument("--json", action="store_true", help="Accepted for symmetry; output is always JSON")
    e.set_defaults(func=cmd_assignments)
    r = ss.add_parser("report", help="Merge-quality report (read-only; titles on stdout by design)")
    r.add_argument("--json", action="store_true", help="Machine-readable report on stdout")
    r.add_argument("--redact", action="store_true", help="Replace titles and class names by stable short hashes")
    r.set_defaults(func=cmd_assignments)
    a = ss.add_parser("alias", help="Class aliases used when merging records from different sources")
    asub = a.add_subparsers(dest="alias_command", required=True)
    add = asub.add_parser("add", help="Treat class --from as the class --to for one kid")
    add.add_argument("--kid", required=True)
    add.add_argument("--from", dest="from_name", required=True, help="Alias class name (as a source reports it)")
    add.add_argument("--to", dest="to_name", required=True, help="Canonical class name")
    ls = asub.add_parser("list", help="Show stored aliases")
    ls.add_argument("--kid")
    ls.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_assignments)
    p.set_defaults(func=cmd_assignments)
    return p
