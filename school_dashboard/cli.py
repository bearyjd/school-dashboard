import argparse
import json
import os
import sys

from school_dashboard import state
from school_dashboard import html
from school_dashboard import email
from school_dashboard import digest as _digest
from school_dashboard.gcal import fetch_gcal_events


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


def cmd_email_sync(args: argparse.Namespace) -> None:
    account = args.account or os.environ.get("SCHOOL_EMAIL_ACCOUNT", "")
    if not account:
        print("Error: provide --account or set SCHOOL_EMAIL_ACCOUNT", file=sys.stderr)
        sys.exit(1)

    email.ensure_labels(account)

    print(f"Scanning: {args.query} (max {args.max_results})", file=sys.stderr)
    digest = email.sync_emails(
        account=account,
        query=args.query,
        max_results=args.max_results,
        digest_path=args.digest_file,
        label_scanned=not args.no_label,
    )

    total = digest.get("total", 0)
    skipped = digest.get("skipped", 0)
    relevant = digest.get("actionable_count", 0)
    ctx_bytes = digest.get("_context_bytes", 0)

    print(f"Emails: {total} total, {skipped} skipped, {relevant} relevant", file=sys.stderr)
    print(f"Context: {ctx_bytes:,} bytes ({ctx_bytes/1024:.1f}KB)", file=sys.stderr)

    if args.json:
        print(json.dumps(digest, indent=2))


def cmd_email_show(args: argparse.Namespace) -> None:
    if args.json:
        p = email._digest_path(args.digest_file)
        if p.exists():
            print(p.read_text())
        else:
            print("{}", file=sys.stdout)
    else:
        print(email.digest_summary(args.digest_file))


def cmd_digest(args: argparse.Namespace) -> None:
    litellm_url = os.environ.get("LITELLM_URL", "")
    api_key = os.environ.get("LITELLM_API_KEY", "")
    model = os.environ.get("LITELLM_MODEL", "claude-sonnet")
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")
    db_path = os.environ.get("SCHOOL_DB_PATH", "/app/state/school.db")
    facts_path = os.environ.get("SCHOOL_FACTS_PATH", "/app/state/facts.json")
    gog_account = os.environ.get("GOG_ACCOUNT", "")
    state_file = args.state_file or os.environ.get("SCHOOL_STATE_PATH", "/app/state/school-state.json")

    if not litellm_url:
        print("Error: LITELLM_URL not set", file=sys.stderr)
        sys.exit(1)
    if not ntfy_topic:
        print("Error: NTFY_TOPIC not set", file=sys.stderr)
        sys.exit(1)

    gc_path = os.environ.get("SCHOOL_GC_PATH", "/app/state/gc-schedule.json")
    gcal_events = fetch_gcal_events(gog_account) if gog_account else []

    if args.mode == "morning":
        text, cards = _digest.build_morning_digest(
            state_path=state_file,
            db_path=db_path,
            facts_path=facts_path,
            gcal_events=gcal_events,
            litellm_url=litellm_url,
            api_key=api_key,
            model=model,
            gc_path=gc_path,
        )
        _digest.send_ntfy(topic=ntfy_topic, message=text, title="Morning Briefing",
                          cards=cards, db_path=db_path)

    elif args.mode == "afternoon":
        text, cards = _digest.build_afternoon_digest(
            state_path=state_file,
            db_path=db_path,
            litellm_url=litellm_url,
            api_key=api_key,
            model=model,
            gc_path=gc_path,
        )
        _digest.send_ntfy(topic=ntfy_topic, message=text, title="Homework Check",
                          cards=cards, db_path=db_path)

    elif args.mode == "night":
        text, cards = _digest.build_night_digest(
            state_path=state_file,
            db_path=db_path,
            facts_path=facts_path,
            gcal_events=gcal_events,
            litellm_url=litellm_url,
            api_key=api_key,
            model=model,
            gc_path=gc_path,
        )
        _digest.send_ntfy(topic=ntfy_topic, message=text, title="Night Prep",
                          cards=cards, db_path=db_path)

    else:
        raise ValueError(f"Unknown mode: {args.mode}")

    print(f"Digest sent [{args.mode}]: {len(cards)} cards, {text[:80]}...", file=sys.stderr)


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

    p_email = subs.add_parser("email-sync", help="Fetch and pre-process emails into compact digest")
    p_email.add_argument("--account", type=str, default=None, help="Gmail account (or set SCHOOL_EMAIL_ACCOUNT)")
    p_email.add_argument("--query", type=str, default="in:inbox newer_than:12h", help="Gmail search query")
    p_email.add_argument("--max", type=int, default=50, dest="max_results", help="Max emails to fetch")
    p_email.add_argument("--no-label", action="store_true", help="Skip labeling processed emails")
    p_email.add_argument("--digest-file", type=str, default=None, help="Output digest JSON path")
    p_email.add_argument("--json", action="store_true")
    p_email.set_defaults(func=cmd_email_sync)

    p_email_show = subs.add_parser("email-show", help="Print email digest summary")
    p_email_show.add_argument("--digest-file", type=str, default=None)
    p_email_show.add_argument("--json", action="store_true")
    p_email_show.set_defaults(func=cmd_email_show)

    p_digest = subs.add_parser("digest", help="Build and send a timed digest notification")
    p_digest.add_argument(
        "--mode",
        choices=["morning", "afternoon", "night"],
        required=True,
        help="Which digest to send",
    )
    p_digest.set_defaults(func=cmd_digest)

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
