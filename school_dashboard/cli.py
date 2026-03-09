import argparse
import json
import sys

from school_dashboard import state
from school_dashboard import html


def cmd_update(args: argparse.Namespace) -> None:
    s = state.load(args.state_file)

    ixl_count = state.update_from_ixl_files(s, args.ixl_dir)
    sgy_count = state.update_from_sgy_file(s, args.sgy_file)
    pruned = state.prune_stale(s)

    p = state.save(s, args.state_file)
    print(f"Updated: {ixl_count} IXL children, {sgy_count} SGY children, {pruned} stale items pruned", file=sys.stderr)
    print(f"State saved: {p}", file=sys.stderr)


def cmd_html(args: argparse.Namespace) -> None:
    s = state.load(args.state_file)
    out = html.render(s, args.output)
    print(f"Dashboard: {out}", file=sys.stderr)


def cmd_show(args: argparse.Namespace) -> None:
    s = state.load(args.state_file)
    if args.json:
        print(json.dumps(s, indent=2))
    else:
        print(state.summary_text(s))


def cmd_action_add(args: argparse.Namespace) -> None:
    s = state.load(args.state_file)
    item = state.add_action_item(
        s,
        child=args.child,
        source=args.source or "manual",
        item_type=args.type or "task",
        summary=args.summary,
        due=args.due,
    )
    state.save(s, args.state_file)
    print(f"Added: [{item['id']}] {item['summary']}")


def cmd_action_complete(args: argparse.Namespace) -> None:
    s = state.load(args.state_file)
    if state.complete_action_item(s, args.id):
        state.save(s, args.state_file)
        print(f"Completed: {args.id}")
    else:
        print(f"Not found: {args.id}", file=sys.stderr)
        sys.exit(1)


def cmd_action_list(args: argparse.Namespace) -> None:
    s = state.load(args.state_file)
    items = state.pending_action_items(s, child=args.child)
    if args.json:
        print(json.dumps(items, indent=2))
        return
    if not items:
        print("No pending action items.")
        return
    for item in items:
        due = f" (due {item['due'][:10]})" if item.get("due") else ""
        src = f" [{item['source']}]" if item.get("source") else ""
        print(f"  {item['id']}  {item['child']}: {item['summary']}{due}{src}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="school-state", description="School situational awareness state manager")
    parser.add_argument("--state-file", type=str, default=None, help="State file path (default: /var/lib/openclaw/school-state.json)")

    subs = parser.add_subparsers(dest="command")

    p_update = subs.add_parser("update", help="Merge latest scraper output into state")
    p_update.add_argument("--ixl-dir", default="/tmp/ixl", help="IXL output directory")
    p_update.add_argument("--sgy-file", default="/tmp/schoology-daily.json", help="SGY summary JSON file")
    p_update.set_defaults(func=cmd_update)

    p_html = subs.add_parser("html", help="Regenerate static dashboard")
    p_html.add_argument("--output", type=str, default=None, help="Output HTML path")
    p_html.set_defaults(func=cmd_html)

    p_show = subs.add_parser("show", help="Print current state summary")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_action = subs.add_parser("action", help="Manage action items")
    action_subs = p_action.add_subparsers(dest="action_command")

    p_add = action_subs.add_parser("add", help="Add an action item")
    p_add.add_argument("child", help="Child name")
    p_add.add_argument("summary", help="Action item description")
    p_add.add_argument("--due", type=str, default=None)
    p_add.add_argument("--source", type=str, default=None)
    p_add.add_argument("--type", type=str, default=None)
    p_add.set_defaults(func=cmd_action_add)

    p_complete = action_subs.add_parser("complete", help="Mark an action item completed")
    p_complete.add_argument("id", help="Action item ID")
    p_complete.set_defaults(func=cmd_action_complete)

    p_list = action_subs.add_parser("list", help="List pending action items")
    p_list.add_argument("--child", type=str, default=None)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_action_list)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "action" and not getattr(args, "action_command", None):
        p_action.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
