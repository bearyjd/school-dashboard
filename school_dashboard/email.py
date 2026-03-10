import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from school_dashboard.state import _state_path, get_children

DEFAULT_DIGEST_PATH = None  # derived from state path
GOG_ACCOUNT = os.environ.get("SCHOOL_EMAIL_ACCOUNT", "")
SNIPPET_BODY_CHARS = 2000
SNIPPET_ATTACHMENT_CHARS = 1000

SKIP_CATEGORIES = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS", "SPAM", "TRASH"}
SKIP_SENDERS = {
    "noreply@github.com", "notifications@github.com",
    "no-reply@accounts.google.com",
}

SCHOOL_DOMAINS = set(os.environ.get("SCHOOL_DOMAINS", "stmark.org,schoology.com,ccsend.com").split(","))

ACTIVITY_KEYWORDS = {
    "practice", "game", "schedule", "roster", "team", "tournament", "tryout",
    "match", "season", "rehearsal", "performance", "meet", "race", "scrimmage",
    "playoff", "camp", "clinic", "uniform", "dues", "permission", "field trip",
    "volunteer", "fundraiser", "picture day", "spirit day", "dress down",
}

FINANCIAL_KEYWORDS = {
    "invoice", "payment", "receipt", "bill", "balance", "tuition", "fee",
    "charge", "refund", "statement",
}


def _digest_path(override: Optional[str] = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("SCHOOL_EMAIL_DIGEST")
    if env:
        return Path(env)
    return _state_path().parent / "email-digest.json"


def _run_gog(args: list[str], timeout: int = 30) -> Optional[dict]:
    cmd = ["gog"] + args
    env = os.environ.copy()
    env.setdefault("GOG_KEYRING_PASSWORD", "")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _sender_domain(from_addr: str) -> str:
    m = re.search(r"@([\w.-]+)>?\s*$", from_addr)
    return m.group(1).lower() if m else ""


def _sender_email(from_addr: str) -> str:
    m = re.search(r"<([^>]+)>", from_addr)
    if m:
        return m.group(1).lower()
    m = re.search(r"([\w.+-]+@[\w.-]+)", from_addr)
    return m.group(1).lower() if m else from_addr.lower().strip()


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_pdf_text(pdf_path: str, max_chars: int = SNIPPET_ATTACHMENT_CHARS) -> str:
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(pdf_path)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception:
        return ""


def _classify(from_addr: str, subject: str, labels: list[str]) -> str:
    domain = _sender_domain(from_addr)
    email = _sender_email(from_addr)
    subj_lower = subject.lower()

    if any(cat in labels for cat in SKIP_CATEGORIES):
        return "SKIP"
    if email in SKIP_SENDERS:
        return "SKIP"

    if "STARRED" in labels:
        return "STARRED"

    if domain in SCHOOL_DOMAINS:
        return "SCHOOL"

    children = get_children()
    child_names = {n.lower() for n in children}
    if any(name in subj_lower or name in from_addr.lower() for name in child_names):
        return "CHILD_ACTIVITY"

    if any(kw in subj_lower for kw in ACTIVITY_KEYWORDS):
        return "CHILD_ACTIVITY"

    if any(kw in subj_lower for kw in FINANCIAL_KEYWORDS):
        return "FINANCIAL"

    return "UNKNOWN"


def fetch_emails(
    account: str,
    query: str = "in:inbox newer_than:12h",
    max_results: int = 50,
    exclude_label: Optional[str] = None,
) -> list[dict]:
    search_query = query
    if exclude_label:
        search_query += f" -label:{exclude_label}"

    data = _run_gog([
        "gmail", "search", search_query,
        "--max", str(max_results),
        "-a", account, "-j",
    ], timeout=60)
    if not data:
        return []

    threads = data if isinstance(data, list) else data.get("threads", data.get("messages", []))
    return threads


def fetch_message(account: str, msg_id: str) -> Optional[dict]:
    return _run_gog(["gmail", "get", msg_id, "-a", account, "-j"], timeout=30)


def download_attachment(
    account: str, msg_id: str, attachment_id: str, out_dir: str, filename: str,
) -> Optional[str]:
    out_path = os.path.join(out_dir, filename)
    result = _run_gog([
        "gmail", "attachment", msg_id, attachment_id,
        "-a", account, "--out", out_dir, "--name", filename,
    ], timeout=60)
    if os.path.exists(out_path):
        return out_path
    # gog might save without --name, check dir
    for f in os.listdir(out_dir):
        if f == filename:
            return os.path.join(out_dir, f)
    return None


def normalize_email(account: str, thread: dict, attachment_dir: str) -> dict:
    msg_id = thread["id"]
    from_addr = thread.get("from", "")
    subject = thread.get("subject", "")
    labels = thread.get("labels", [])
    date = thread.get("date", "")

    bucket = _classify(from_addr, subject, labels)

    record = {
        "id": msg_id,
        "from": from_addr,
        "subject": subject,
        "date": date,
        "labels": labels,
        "bucket": bucket,
        "snippet": None,
        "attachments": [],
        "body_size": 0,
    }

    if bucket == "SKIP":
        return record

    full = fetch_message(account, msg_id)
    if not full:
        return record

    # Use Gmail's built-in snippet if available (free, no parsing)
    gmail_snippet = full.get("message", {}).get("snippet", "")

    body_html = full.get("body", "")
    record["body_size"] = len(body_html)

    if body_html:
        body_text = _strip_html(body_html)
        record["snippet"] = body_text[:SNIPPET_BODY_CHARS]
    elif gmail_snippet:
        record["snippet"] = gmail_snippet

    attachments = full.get("attachments", [])
    for att in attachments:
        filename = att.get("filename", "")
        mime = att.get("mimeType", "")
        size = att.get("size", 0)
        att_id = att.get("attachmentId", "")

        if not filename or not att_id:
            continue

        # Skip inline images under 50KB (logos, signatures)
        if mime.startswith("image/") and size < 50_000:
            continue

        # Skip large files (>5MB)
        if size > 5_000_000:
            continue

        att_record = {
            "filename": filename,
            "mimeType": mime,
            "size": size,
            "text_snippet": None,
        }

        # Extract text from PDFs
        if mime == "application/pdf" or filename.lower().endswith(".pdf"):
            msg_dir = os.path.join(attachment_dir, msg_id)
            os.makedirs(msg_dir, exist_ok=True)
            local_path = download_attachment(account, msg_id, att_id, msg_dir, filename)
            if local_path:
                text = _extract_pdf_text(local_path)
                if text:
                    att_record["text_snippet"] = text

        # Extract text from .txt/.csv/.ics
        elif mime in ("text/plain", "text/csv", "text/calendar") or filename.lower().endswith((".txt", ".csv", ".ics")):
            msg_dir = os.path.join(attachment_dir, msg_id)
            os.makedirs(msg_dir, exist_ok=True)
            local_path = download_attachment(account, msg_id, att_id, msg_dir, filename)
            if local_path:
                try:
                    text = Path(local_path).read_text(errors="replace")
                    att_record["text_snippet"] = text[:SNIPPET_ATTACHMENT_CHARS]
                except OSError:
                    pass

        # Images > 50KB: note for potential vision processing
        elif mime.startswith("image/"):
            msg_dir = os.path.join(attachment_dir, msg_id)
            os.makedirs(msg_dir, exist_ok=True)
            local_path = download_attachment(account, msg_id, att_id, msg_dir, filename)
            if local_path:
                att_record["local_path"] = local_path

        record["attachments"].append(att_record)

    return record


def label_processed(account: str, thread_id: str, label_name: str) -> bool:
    result = _run_gog([
        "gmail", "labels", "modify", thread_id,
        "--add", label_name,
        "-a", account,
    ], timeout=15)
    return result is not None


def ensure_labels(account: str) -> None:
    for label in ["OpenClaw/Scanned", "OpenClaw/Processed"]:
        _run_gog(["gmail", "labels", "create", label, "-a", account], timeout=15)


def sync_emails(
    account: str,
    query: str = "in:inbox newer_than:12h",
    max_results: int = 50,
    digest_path: Optional[str] = None,
    attachment_dir: Optional[str] = None,
    label_scanned: bool = True,
) -> dict:
    if not account:
        return {"error": "No email account configured. Set SCHOOL_EMAIL_ACCOUNT env var."}

    out_path = _digest_path(digest_path)
    att_dir = attachment_dir or str(out_path.parent / "email-attachments")
    os.makedirs(att_dir, exist_ok=True)

    threads = fetch_emails(account, query, max_results, exclude_label="OpenClaw/Scanned")
    if not threads:
        digest = {
            "scan_time": datetime.now().isoformat(),
            "query": query,
            "total": 0,
            "skipped": 0,
            "emails": [],
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(digest, indent=2))
        return digest

    emails = []
    skipped = 0

    for thread in threads:
        record = normalize_email(account, thread, att_dir)
        if record["bucket"] == "SKIP":
            skipped += 1

        emails.append(record)

        if label_scanned:
            # Get thread ID from full message if available, fallback to id
            thread_id = thread.get("id", "")
            if thread_id:
                label_processed(account, thread_id, "OpenClaw/Scanned")

    digest = {
        "scan_time": datetime.now().isoformat(),
        "query": query,
        "total": len(emails),
        "skipped": skipped,
        "actionable_count": sum(1 for e in emails if e["bucket"] not in ("SKIP", "UNKNOWN")),
        "emails": emails,
    }

    # Context budget check
    digest_json = json.dumps(digest, indent=2)
    digest["_context_bytes"] = len(digest_json)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(digest, indent=2))

    return digest


def digest_summary(digest_path: Optional[str] = None) -> str:
    p = _digest_path(digest_path)
    if not p.exists():
        return "No email digest found. Run: school-state email-sync"

    try:
        digest = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return "Email digest is corrupted."

    lines = [
        f"Email scan: {digest.get('scan_time', 'unknown')}",
        f"Total: {digest.get('total', 0)}, Skipped: {digest.get('skipped', 0)}, "
        f"Relevant: {digest.get('actionable_count', 0)}",
        "",
    ]

    for email in digest.get("emails", []):
        if email["bucket"] == "SKIP":
            continue

        bucket_icon = {
            "SCHOOL": "🏫", "CHILD_ACTIVITY": "⚽", "STARRED": "⭐",
            "FINANCIAL": "💰", "UNKNOWN": "📬",
        }.get(email["bucket"], "📧")

        line = f"{bucket_icon} [{email['bucket']}] {email['subject']}"
        if email.get("snippet"):
            snippet = email["snippet"][:150].replace("\n", " ")
            line += f"\n   {snippet}"
        if email.get("attachments"):
            att_names = [a["filename"] for a in email["attachments"]]
            line += f"\n   📎 {', '.join(att_names)}"
            for a in email["attachments"]:
                if a.get("text_snippet"):
                    line += f"\n   📄 {a['filename']}: {a['text_snippet'][:100]}..."

        lines.append(line)
        lines.append("")

    context_bytes = digest.get("_context_bytes", 0)
    lines.append(f"Context budget: {context_bytes:,} bytes ({context_bytes/1024:.1f}KB)")

    return "\n".join(lines)
