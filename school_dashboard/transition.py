"""`school-state transition ...`: maintenance mode, backup, restore and dry-run."""
import argparse
import json
import sys
from pathlib import Path

from school_dashboard import backup, dry_run, maintenance

EXIT_USAGE = 2
EXIT_INACTIVE = 10  # `maintenance check`: 0 active, 10 inactive; anything else = failure


def add_parser(subs) -> argparse.ArgumentParser:
    p = subs.add_parser("transition", help="Maintenance mode, backup and restore")
    ts = p.add_subparsers(dest="transition_command")

    p_m = ts.add_parser("maintenance", help="Control maintenance mode")
    ms = p_m.add_subparsers(dest="maintenance_command")
    p_on = ms.add_parser("on", help="Enable maintenance mode (idempotent)")
    p_on.add_argument("--reason", default="", help="Why maintenance is on")
    ms.add_parser("off", help="Disable maintenance mode")
    ms.add_parser("status", help="Show maintenance status")
    ms.add_parser("check", help="Exit 0 = ACTIVE, 10 = inactive; ANY other code means the check failed (callers must treat it as active)")
    p_m.set_defaults(func=cmd_transition)

    p_b = ts.add_parser("backup", help="Back up state (requires maintenance mode)")
    p_b.add_argument("--dest", type=Path, default=None, help="Backup directory (default: <state>/backups/<UTC timestamp>)")
    p_b.add_argument("--wait-idle", type=float, default=0, metavar="SECONDS",
                     help="Wait up to SECONDS for a running sync to finish (default 0: refuse)")
    p_b.set_defaults(func=cmd_transition)

    p_r = ts.add_parser("restore", help="Verify and restore a backup")
    p_r.add_argument("backup_dir", type=Path)
    p_r.add_argument("--target", type=Path, required=True, help="Directory to restore into")
    p_r.add_argument("--confirm-live", action="store_true", help="Allow restoring over the live state dir")
    p_r.set_defaults(func=cmd_transition)

    p_d = ts.add_parser("dry-run", help="Read-only year-transition report (no writes, works with or without maintenance)")
    p_d.add_argument("--to-year", required=True, help="Target school year, YYYY-YY (e.g. 2026-27)")
    p_d.add_argument("--from-year", default=None, help="Current school year (default: derived from today, Aug start)")
    p_d.add_argument("--archive-dest", type=Path, default=None,
                     help="Planned archive dir (default: <state>/archive/<from>-to-<to>); validated, never created")
    p_d.add_argument("--json", action="store_true", help="Machine-readable output")
    p_d.set_defaults(func=cmd_transition)
    p.set_defaults(func=cmd_transition)
    return p


def _maintenance(args: argparse.Namespace) -> int:
    cmd = getattr(args, "maintenance_command", None)
    if cmd == "on":
        info = maintenance.enable(args.reason)
        print(f"Maintenance ON (operation {info['operation_id']})", file=sys.stderr)
        if maintenance.sync_in_flight():
            print("Warning: a sync appears to be running (sync.running); it will stop at its next "
                  "step. Use 'backup --wait-idle SECONDS' to wait for it.", file=sys.stderr)
        return 0
    if cmd == "off":
        removed = maintenance.disable()
        print("Maintenance OFF" if removed else "Maintenance was not active", file=sys.stderr)
        return 0
    if cmd == "status":
        info = maintenance.status()
        print(json.dumps({"active": info is not None, **(info or {})}, indent=2))
        return 0
    if cmd == "check":
        return 0 if maintenance.is_active() else EXIT_INACTIVE
    print("usage: school-state transition maintenance on|off|status|check", file=sys.stderr)
    return EXIT_USAGE


def _backup(args: argparse.Namespace) -> int:
    dest = backup.create_backup(args.dest, args.wait_idle)
    print(f"Backup written: {dest}")
    return 0


def _restore(args: argparse.Namespace) -> int:
    report = backup.restore_backup(args.backup_dir, args.target, args.confirm_live)
    counts = " ".join(f"{k}={v}" for k, v in sorted(report["counts"].items()))
    print(f"Restore ok: {len(report['files'])} files -> {args.target} ({counts})")
    if report["snapshot"]:
        print(f"Previous state saved in {report['snapshot']}")
    return 0


def _dry_run(args: argparse.Namespace) -> int:
    try:
        report = dry_run.build_report(args.to_year, args.from_year, args.archive_dest)
    except dry_run.DryRunArgError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps(report, indent=2) if args.json else dry_run.render_text(report), end="\n" if args.json else "")
    return 0


def cmd_transition(args: argparse.Namespace) -> None:
    sub = getattr(args, "transition_command", None)
    handlers = {"maintenance": _maintenance, "backup": _backup, "restore": _restore, "dry-run": _dry_run}
    if sub not in handlers:
        print("usage: school-state transition maintenance|backup|restore|dry-run", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    try:
        code = handlers[sub](args)
    except maintenance.MaintenanceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except backup.BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(exc.code)
    if code:
        sys.exit(code)
