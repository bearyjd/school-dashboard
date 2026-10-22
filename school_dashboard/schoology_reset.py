"""`school-state schoology reset`: archive stale Schoology data and rebuild from one scrape.

Archives every pending schoology action item that the latest scrape no longer
produces, plus the whole old `schoology` section, then rewrites both from the
scrape. Non-schoology items, completed items and all other state are untouched.
Fails closed: any problem before the swap leaves the state file unchanged.
"""
import argparse
import json
import os
import secrets
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from school_dashboard import maintenance, paths, state, statelock
from school_dashboard.atomic import mkdir_private, write_atomic

EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_MAINTENANCE = 3  # same code cli.py uses for blocked write commands
SOURCE = "schoology"
MAX_SGY_BYTES = 20 * 1024 * 1024
WARNING_PREVIEW = 3
WARNING_CHARS = 100


class ResetError(Exception):
    def __init__(self, message: str, code: int = EXIT_USAGE):
        super().__init__(message)
        self.code = code


def _load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise ResetError(f"{label} not found: {path}")
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ResetError(f"{label} unreadable ({path}): {exc}")
    except (RecursionError, MemoryError):
        raise ResetError(f"{label} too deeply nested or too large to parse ({path})")


def _valid_entry(entry: object) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("child"), dict):
        return False
    name = entry["child"].get("name")
    if not isinstance(name, str) or not name.split():
        return False
    for key in ("assignments", "grades", "announcements"):
        value = entry.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            return False
    return all(isinstance(a.get("title"), str) and a["title"] for a in entry.get("assignments", []))


def load_sgy(path: Path, max_age_hours: float) -> tuple:
    """(validated per_child entries, scraper warnings) of an `sgy summary --json` file."""
    if not path.exists():
        raise ResetError(f"sgy file not found: {path}")
    if path.stat().st_size > MAX_SGY_BYTES:
        raise ResetError(f"sgy file {path} is too large (> {MAX_SGY_BYTES} bytes)")
    age_h = (time.time() - path.stat().st_mtime) / 3600
    if age_h > max_age_hours:
        raise ResetError(f"sgy file {path} is too old ({age_h:.1f}h > {max_age_hours}h); run a fresh scrape")
    data = _load_json(path, "sgy file")
    per_child = data.get("per_child") if isinstance(data, dict) else None
    if not isinstance(per_child, list) or not per_child:
        raise ResetError("sgy file has no per_child entries; refusing to wipe state from an empty scrape")
    if not all(_valid_entry(e) for e in per_child):
        raise ResetError("sgy file has malformed per_child entries "
                         "(need child.name, assignments with a non-empty string title, lists of objects)")
    warnings = data.get("warnings")
    return per_child, (warnings if isinstance(warnings, list) else [])


def load_state(path: Path) -> dict:
    data = _load_json(path, "state file")
    if not isinstance(data, dict):
        raise ResetError("state file must be a JSON object")
    items = data.get("action_items")
    if not isinstance(items, list) or not all(isinstance(i, dict) and "id" in i for i in items):
        raise ResetError("state action_items must be a list of items with ids")
    if not isinstance(data.get("schoology", {}), dict):
        raise ResetError("state schoology section must be an object")
    return data


def _new_section(old: dict, per_child: list, now: str) -> dict:
    """Section built like update_schoology; an entry equal to the old one keeps its old timestamp."""
    scratch: dict = {"schoology": {}}
    for entry in per_child:
        name = state.canonical_name(entry["child"]["name"].split()[0])
        state.update_schoology(scratch, name, entry)
    section = {}
    for name, new in scratch["schoology"].items():
        prior = old.get(name)
        same = isinstance(prior, dict) and {**prior, "updated": None} == {**new, "updated": None}
        section[name] = prior if same else {**new, "updated": now}
    return section


def _scrape_items(per_child: list, now: str) -> dict:
    """id -> item, as add_action_item would create them (later duplicates only fill a missing due)."""
    items: dict = {}
    for entry in per_child:
        name = state.canonical_name(entry["child"]["name"].split()[0])
        for a in entry.get("assignments", []):
            summary = a.get("title", "Unknown")
            due = a.get("due_date") if isinstance(a.get("due_date"), str) and a.get("due_date") else None
            aid = state._action_id(SOURCE, summary, name)
            if aid in items:
                if due and not items[aid]["due"]:
                    items[aid] = {**items[aid], "due": due}
                continue
            items[aid] = {"id": aid, "child": name, "source": SOURCE, "type": "assignment",
                          "summary": summary, "due": due, "status": "pending", "created": now}
    return items


def _is_old_pending(item: dict) -> bool:
    return item.get("source") == SOURCE and item.get("status") == "pending"


def _count_by_child(items: list) -> dict:
    counts: dict = {}
    for i in items:
        counts[i.get("child", "")] = counts.get(i.get("child", ""), 0) + 1
    return dict(sorted(counts.items()))


def _merge_items(old_items: list, wanted: dict) -> tuple:
    """(new action_items, archived, kept count, added count). Pure."""
    existing_ids = {i["id"] for i in old_items}
    result, archived, kept = [], [], 0
    for item in old_items:
        if not _is_old_pending(item):
            result.append(item)
        elif item["id"] in wanted:
            fill = wanted[item["id"]]["due"]
            result.append({**item, "due": fill} if fill and not item.get("due") else item)
            kept += 1
        else:
            archived.append(item)
    added = [v for k, v in wanted.items() if k not in existing_ids]
    return [*result, *added], archived, kept, len(added)


def compute_reset(old: dict, per_child: list, now: str) -> dict:
    """Pure: the new state plus what is archived. Never mutates `old`."""
    old_section = old.get("schoology", {})
    old_items = old["action_items"]
    completed_ids = {i["id"] for i in old_items if not _is_old_pending(i) and i.get("status") == "completed"}
    wanted = {k: v for k, v in _scrape_items(per_child, now).items() if k not in completed_ids}
    items, archived, kept, added = _merge_items(old_items, wanted)
    section = _new_section(old_section, per_child, now)
    new_state = {**old, "schoology": section, "action_items": items}
    section_changed = section != old_section
    changed = bool(archived) or added > 0 or new_state != old
    return {
        "new_state": new_state, "archived": archived, "kept": kept, "added": added,
        "old_section": old_section, "section_changed": section_changed, "changed": changed,
        "old_pending": _count_by_child([i for i in old_items if _is_old_pending(i)]),
        "new_pending": _count_by_child([i for i in items if _is_old_pending(i)]),
        "dropped": sorted(set(old_section) - set(section)),
    }


def _archive_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"schoology-{stamp}-{secrets.token_hex(3)}.json"


def _secure_dir(d: Path) -> None:
    """An existing archive dir must be ours and no looser than 0700."""
    st = d.stat()
    if not stat.S_ISDIR(st.st_mode):
        raise ResetError(f"archive dir is not a directory: {d}", EXIT_FAILED)
    if st.st_uid != os.getuid():
        raise ResetError(f"archive dir {d} is not owned by this user", EXIT_FAILED)
    if stat.S_IMODE(st.st_mode) & 0o077:
        os.chmod(d, 0o700)


def _create_exclusive(path: Path, text: str) -> None:
    """Publish `text` at `path` only if it does not exist (temp file + os.link)."""
    tmp = path.with_name(f".{path.name}.new")
    try:
        write_atomic(tmp, text)
        os.link(tmp, path)  # raises FileExistsError rather than overwriting
    finally:
        tmp.unlink(missing_ok=True)


def _write_archive(archive_dir: Path, plan: dict, state_file: Path, sgy_file: Path, now: str) -> Path:
    if not archive_dir.exists():
        mkdir_private(archive_dir)
    _secure_dir(archive_dir)
    path = archive_dir / _archive_name()
    payload = {
        "created_at": now, "state_file": str(state_file), "sgy_file": str(sgy_file),
        "counts": {"items": len(plan["archived"]), "section_children": len(plan["old_section"])},
        "items": plan["archived"], "schoology_section": plan["old_section"],
    }
    text = json.dumps(payload, indent=2)
    try:
        _create_exclusive(path, text)
        back = json.loads(path.read_text())
    except FileExistsError:
        raise ResetError(f"archive already exists, not overwriting: {path}; state unchanged", EXIT_FAILED)
    except (OSError, json.JSONDecodeError) as exc:
        raise ResetError(f"archive could not be written/verified ({path}): {exc}; state unchanged", EXIT_FAILED)
    if back != payload or back["counts"]["items"] != len(back["items"]):
        raise ResetError(f"archive verification failed ({path}); state unchanged", EXIT_FAILED)
    return path


def _snapshot(path: Path) -> tuple:
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


def _swap_state(state_file: Path, new_state: dict, now: str, archive: Optional[Path], snap: tuple) -> None:
    where = f" (archive kept at {archive})" if archive else ""
    if _snapshot(state_file) != snap:
        raise ResetError(f"state file changed during reset; aborting, state unchanged{where}", EXIT_FAILED)
    mode = stat.S_IMODE(state_file.stat().st_mode)
    try:
        write_atomic(state_file, json.dumps({**new_state, "last_updated": now}, indent=2), mode)
    except OSError as exc:
        raise ResetError(f"could not write state: {exc}; state unchanged{where}", EXIT_FAILED)


def _report(plan: dict, dry_run: bool, archive: Optional[Path], state_file: Path, sgy_file: Path,
            warnings: list) -> dict:
    return {
        "dry_run": dry_run, "changed": plan["changed"], "state_file": str(state_file),
        "sgy_file": str(sgy_file), "old_pending_by_child": plan["old_pending"],
        "new_pending_by_child": plan["new_pending"], "archived_items": len(plan["archived"]),
        "items_kept": plan["kept"], "items_added": plan["added"],
        "new_schoology_children": sorted(plan["new_state"]["schoology"]),
        "dropped_alias_keys": plan["dropped"], "archive_path": str(archive) if archive else None,
        "warnings_count": len(warnings),
        "warnings_preview": [str(w)[:WARNING_CHARS] for w in warnings[:WARNING_PREVIEW]],
    }


def _compute_checked(old: dict, per_child: list, now: str) -> dict:
    try:
        return compute_reset(old, per_child, now)
    except (AttributeError, TypeError, KeyError) as exc:
        raise ResetError(f"sgy file has unexpected structure ({type(exc).__name__}: {exc})")


def _preflight(dry_run: bool) -> None:
    if dry_run:
        return
    if not maintenance.is_active():
        raise ResetError("maintenance mode is not active; run: school-state transition maintenance on",
                         EXIT_MAINTENANCE)
    if maintenance.sync_in_flight():
        raise ResetError("a sync appears to be running (sync.running marker present); "
                         "wait for it to finish and retry",
                         EXIT_MAINTENANCE)


def run_reset(sgy_file, archive_dir=None, max_age_hours: float = 24, dry_run: bool = False,
              state_file=None, allow_warnings: bool = False) -> dict:
    sgy_file = Path(sgy_file)
    state_file = Path(state_file) if state_file else paths.state_path()
    _preflight(dry_run)
    per_child, warnings = load_sgy(sgy_file, max_age_hours)
    if warnings and not dry_run and not allow_warnings:
        raise ResetError(f"sgy file carries {len(warnings)} scraper warning(s), e.g. "
                         f"{str(warnings[0])[:WARNING_CHARS]!r}; fix the scrape or pass --allow-warnings")
    old = load_state(state_file)
    snap = _snapshot(state_file)
    now = datetime.now().isoformat()
    plan = _compute_checked(old, per_child, now)
    archive = None
    if plan["changed"] and not dry_run:
        if plan["archived"] or plan["old_section"] != plan["new_state"]["schoology"]:
            target = Path(archive_dir) if archive_dir else state_file.parent / "archive"
            archive = _write_archive(target, plan, state_file, sgy_file, now)
        _swap_state(state_file, plan["new_state"], now, archive, snap)
    return _report(plan, dry_run, archive, state_file, sgy_file, warnings)


def _by_child(counts: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in counts.items()) or "none"


def render_text(rep: dict) -> str:
    if not rep["changed"]:
        return "Schoology reset: nothing to do (state already matches the scrape)\n"
    w = rep["dry_run"]
    lines = [
        "Schoology reset " + ("(dry run, nothing written)" if w else "complete"),
        f"  Pending schoology items before: {_by_child(rep['old_pending_by_child'])}",
        f"  Pending schoology items after:  {_by_child(rep['new_pending_by_child'])}",
        f"  Items {'that would be ' if w else ''}archived: {rep['archived_items']}",
        f"  Items kept: {rep['items_kept']}, {'would add' if w else 'added'}: {rep['items_added']}",
        f"  Schoology section children: {', '.join(rep['new_schoology_children'])}",
        f"  Stale alias keys {'that would be dropped' if w else 'dropped'}: {', '.join(rep['dropped_alias_keys']) or 'none'}",
    ]
    if rep["archive_path"]:
        lines.append(f"  Archive: {rep['archive_path']}")
    return "\n".join(lines + _warning_lines(rep)) + "\n"


def _warning_lines(rep: dict) -> list:
    if not rep["warnings_count"]:
        return []
    return [f"  Scraper warnings: {rep['warnings_count']}"] + [f"    - {w}" for w in rep["warnings_preview"]]


def add_parser(subs) -> argparse.ArgumentParser:
    p = subs.add_parser("schoology", help="Schoology state maintenance")
    ss = p.add_subparsers(dest="schoology_command")
    r = ss.add_parser("reset", help="Archive stale Schoology data and rebuild it from one scrape")
    r.add_argument("--sgy-file", type=Path, required=True, help="`sgy summary --json` output")
    r.add_argument("--archive-dir", type=Path, default=None, help="Default: <state dir>/archive")
    r.add_argument("--max-age-hours", type=float, default=24, help="Refuse an older sgy file (default 24)")
    r.add_argument("--allow-warnings", action="store_true", help="Proceed although the scrape reports warnings")
    r.add_argument("--dry-run", action="store_true", help="Read-only report; works without maintenance mode")
    r.add_argument("--json", action="store_true", help="Machine-readable output")
    r.set_defaults(func=cmd_schoology)
    p.set_defaults(func=cmd_schoology)
    return p


def _run_from_args(args: argparse.Namespace) -> dict:
    return run_reset(args.sgy_file, args.archive_dir, args.max_age_hours, args.dry_run, args.state_file,
                     args.allow_warnings)


def cmd_schoology(args: argparse.Namespace) -> None:
    if getattr(args, "schoology_command", None) != "reset":
        print("usage: school-state schoology reset --sgy-file PATH [--dry-run] [--json]", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    try:
        if args.dry_run:
            rep = _run_from_args(args)
        else:
            with statelock.state_lock(Path(args.state_file) if args.state_file else paths.state_path()):
                rep = _run_from_args(args)
    except (statelock.StateLockTimeout, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(EXIT_FAILED)
    except ResetError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(exc.code)
    print(json.dumps(rep, indent=2) if args.json else render_text(rep), end="\n" if args.json else "")
