"""`school-state assignments migrate|export` argparse wiring and exit codes."""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from datetime import datetime
from typing import Any

from school_dashboard import paths
from school_dashboard.assignments import migrate

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


def cmd_assignments(args: argparse.Namespace) -> None:
    handlers = {"migrate": _migrate, "export": _export}
    handler = handlers.get(getattr(args, "assignments_command", None))
    if handler is None:
        sys.stderr.write("usage: school-state assignments {migrate|export} [--json]\n")
        sys.exit(EXIT_FAILED)
    try:
        code = handler(args)
    except migrate.MigrationRefused as exc:
        sys.stderr.write(f"Error: {exc}\n")
        code = EXIT_REFUSED
    except migrate.MigrationInputError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        code = EXIT_INPUT
    except migrate.MigrationError as exc:
        sys.stderr.write(f"Error: {exc}\n")
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
    p.set_defaults(func=cmd_assignments)
    return p
