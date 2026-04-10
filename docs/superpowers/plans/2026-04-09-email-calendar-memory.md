# Email + Calendar + Memory Intelligence Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent SQLite event store + facts.json, parse the SMCS school calendar PDF, wire email intel extraction via LiteLLM, and upgrade the morning digest to send structured 7-day lookahead via ntfy + email.

**Architecture:** SQLite `school.db` holds structured calendar/email events; `facts.json` holds amorphous learned facts. A one-time `calendar_import.py` seeds the DB from the PDF. `intel.py` runs at each sync to extract events/facts from classified emails via LiteLLM. The morning digest queries the DB and posts to ntfy (with Email header for Bryn). The chat `/api/chat` gains 30-day lookahead context.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), pypdf (already installed), requests, Flask; gog CLI for Gmail; ntfy.sh for push + email delivery; LiteLLM at https://llm.grepon.cc.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| CREATE | `/opt/school/dashboard/school_dashboard/db.py` | SQLite schema, insert/query helpers, facts.json read/write |
| CREATE | `/opt/school/dashboard/school_dashboard/calendar_import.py` | Parse PDF → structured events → school.db |
| CREATE | `/opt/school/dashboard/school_dashboard/intel.py` | Post classified emails to LiteLLM → extract events/facts |
| CREATE | `/opt/school/dashboard/school_dashboard/digest.py` | Build morning digest text from DB + state, send ntfy + email |
| CREATE | `/opt/school/dashboard/tests/test_db.py` | Unit tests for db.py |
| CREATE | `/opt/school/dashboard/tests/test_calendar_import.py` | Unit tests for PDF parsing logic |
| CREATE | `/opt/school/dashboard/tests/test_intel.py` | Unit tests for intel extraction |
| CREATE | `/opt/school/dashboard/tests/test_digest.py` | Unit tests for digest builder |
| MODIFY | `/opt/school/dashboard/school-sync.sh` | Add intel step; replace ad-hoc digest with `digest.py` call |
| MODIFY | `/opt/school/web/app.py` | Expand `build_system_prompt()` with 30-day events + facts |
| MODIFY | `/opt/school/config/env` | Add `SCHOOL_EMAIL_ACCOUNT`, `SCHOOL_DB_PATH`, `SCHOOL_FACTS_PATH`, `BRYN_EMAIL` |
| MODIFY | `/opt/school/dashboard/cron-prompts/morning-briefing.md` | Replace openclaw placeholders with real values |

---

## Task 1: DB Layer

**Files:**
- Create: `/opt/school/dashboard/school_dashboard/db.py`
- Create: `/opt/school/dashboard/tests/test_db.py`

- [ ] **Step 1.1: Write failing tests**

```bash
cat > /opt/school/dashboard/tests/test_db.py << 'EOF'
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Adjust sys.path so we can import school_dashboard
import sys
sys.path.insert(0, "/opt/school/dashboard")

from school_dashboard.db import (
    init_db,
    insert_event,
    query_upcoming_events,
    load_facts,
    save_fact,
)


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def tmp_facts(tmp_path):
    return str(tmp_path / "facts.json")


def test_init_db_creates_events_table(tmp_db):
    conn = sqlite3.connect(tmp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
    assert cursor.fetchone() is not None
    conn.close()


def test_insert_event_and_query(tmp_db):
    insert_event(tmp_db, "2025-10-13", "No School – Columbus Day", "NO_SCHOOL", child=None, source="calendar_pdf")
    rows = query_upcoming_events(tmp_db, from_date="2025-10-01", days=30)
    assert len(rows) == 1
    assert rows[0]["title"] == "No School – Columbus Day"
    assert rows[0]["type"] == "NO_SCHOOL"


def test_insert_event_dedup(tmp_db):
    insert_event(tmp_db, "2025-10-13", "No School – Columbus Day", "NO_SCHOOL", source="calendar_pdf")
    insert_event(tmp_db, "2025-10-13", "No School – Columbus Day", "NO_SCHOOL", source="email")
    rows = query_upcoming_events(tmp_db, from_date="2025-10-01", days=30)
    assert len(rows) == 1  # second insert ignored due to UNIQUE constraint


def test_query_upcoming_excludes_past(tmp_db):
    insert_event(tmp_db, "2020-01-01", "Old Event", "OTHER", source="calendar_pdf")
    rows = query_upcoming_events(tmp_db, from_date="2025-01-01", days=30)
    assert len(rows) == 0


def test_save_and_load_facts(tmp_facts):
    save_fact(tmp_facts, subject="jack", fact="soccer practice Tuesdays")
    save_fact(tmp_facts, subject="general", fact="principal emails from principal@smcs.org")
    facts = load_facts(tmp_facts)
    assert len(facts) == 2
    assert facts[0]["subject"] == "jack"


def test_save_fact_dedup(tmp_facts):
    save_fact(tmp_facts, subject="jack", fact="Soccer Practice Tuesdays")
    save_fact(tmp_facts, subject="jack", fact="soccer practice tuesdays")  # same, different case
    facts = load_facts(tmp_facts)
    assert len(facts) == 1


def test_load_facts_missing_file(tmp_facts):
    facts = load_facts(tmp_facts)
    assert facts == []
EOF
```

- [ ] **Step 1.2: Run tests — verify they fail**

```bash
cd /opt/school/dashboard && python -m pytest tests/test_db.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'school_dashboard.db'`

- [ ] **Step 1.3: Create db.py**

```bash
cat > /opt/school/dashboard/school_dashboard/db.py << 'EOF'
"""SQLite event store and facts.json helpers for school-dashboard."""
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    title      TEXT NOT NULL,
    type       TEXT NOT NULL DEFAULT 'OTHER',
    child      TEXT,
    source     TEXT NOT NULL DEFAULT 'unknown',
    notes      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedup ON events(date, title);
"""


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def insert_event(
    db_path: str,
    event_date: str,
    title: str,
    event_type: str,
    *,
    child: Optional[str] = None,
    source: str = "unknown",
    notes: Optional[str] = None,
) -> bool:
    """Insert an event. Returns True if inserted, False if duplicate."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO events (date, title, type, child, source, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (event_date, title, event_type, child, source, notes),
        )
        inserted = conn.total_changes > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def query_upcoming_events(db_path: str, from_date: str, days: int = 7) -> list[dict]:
    """Return events from from_date up to from_date + days."""
    start = date.fromisoformat(from_date)
    end = start + timedelta(days=days)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT date, title, type, child, source, notes FROM events "
            "WHERE date >= ? AND date < ? ORDER BY date",
            (from_date, end.isoformat()),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def load_facts(facts_path: str) -> list[dict]:
    """Load facts from JSON file. Returns [] if file missing."""
    p = Path(facts_path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_fact(facts_path: str, subject: str, fact: str, source: str = "email") -> bool:
    """Append a fact if not already present (case-insensitive). Returns True if added."""
    from datetime import datetime
    facts = load_facts(facts_path)
    key = (subject.lower(), fact.lower())
    for existing in facts:
        if (existing["subject"].lower(), existing["fact"].lower()) == key:
            return False
    facts.append({"subject": subject, "fact": fact, "source": source, "learned": datetime.now().date().isoformat()})
    Path(facts_path).write_text(json.dumps(facts, indent=2))
    return True
EOF
```

- [ ] **Step 1.4: Run tests — verify they pass**

```bash
cd /opt/school/dashboard && python -m pytest tests/test_db.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 1.5: Commit**

```bash
cd /opt/school/dashboard && git add school_dashboard/db.py tests/test_db.py && git commit -m "feat: add SQLite event store and facts.json helpers (db.py)"
```

---

## Task 2: Calendar Import

**Files:**
- Create: `/opt/school/dashboard/school_dashboard/calendar_import.py`
- Create: `/opt/school/dashboard/tests/test_calendar_import.py`

- [ ] **Step 2.1: Copy the PDF to the server**

```bash
scp "/var/home/user/Downloads/Pixel9Fold/switch/Download/2025-2026-Parent-Planning-Calendar-updated.pdf" \
    root@192.168.1.14:/opt/school/state/calendar.pdf
```

- [ ] **Step 2.2: Write failing tests**

```bash
cat > /opt/school/dashboard/tests/test_calendar_import.py << 'EOF'
import sys
sys.path.insert(0, "/opt/school/dashboard")

from school_dashboard.calendar_import import (
    parse_month_header,
    classify_event,
    parse_page_events,
)


def test_parse_month_header_august():
    text = "A U G U S T\n2 0 2 5"
    result = parse_month_header(text)
    assert result == ("2025", "08")


def test_parse_month_header_january():
    text = "J A N U A R Y\n2 0 2 6"
    result = parse_month_header(text)
    assert result == ("2026", "01")


def test_classify_no_school():
    assert classify_event("NO SCHOOL - Labor Day") == "NO_SCHOOL"
    assert classify_event("No School for 8th Grade Students") == "NO_SCHOOL"


def test_classify_early_release():
    assert classify_event("Early Release") == "EARLY_RELEASE"
    assert classify_event("12:25 Dismissal") == "EARLY_RELEASE"


def test_classify_mass():
    assert classify_event("MASS") == "MASS"
    assert classify_event("Opening Mass") == "MASS"


def test_classify_assembly():
    assert classify_event("8:30 am Virtue Assembly") == "ASSEMBLY"


def test_classify_parent_mtg():
    assert classify_event("Back to School Night, Grades 5-8 7:00 pm") == "PARENT_MTG"
    assert classify_event("Parent/Teacher Conferences") == "PARENT_MTG"


def test_classify_retreat():
    assert classify_event("Girls' Confirmation Retreat") == "RETREAT"


def test_classify_testing():
    assert classify_event("MAP Growth Testing Week") == "TESTING"


def test_classify_concert():
    assert classify_event("Christmas Program") == "CONCERT"
    assert classify_event("Spring Concert") == "CONCERT"


def test_classify_other():
    assert classify_event("Fall Festival") == "OTHER"


def test_parse_page_events_extracts_dated_items():
    # Simulate a page fragment with known content
    page_text = (
        "SUN MON TUE WED THU FRI SAT\n"
        "31 1     \nNO SCHOOL - Labor Day\n"
        "2\nPreschool\nBegins\n"
        "3\nBack to School Night,\nGrades 5-8\n7:00 pm\n"
        "S E P T E M B E R\n2 0 2 5"
    )
    events = parse_page_events(page_text)
    titles = [e["title"] for e in events]
    assert any("No School" in t or "NO SCHOOL" in t for t in titles)
EOF
```

- [ ] **Step 2.3: Run tests — verify they fail**

```bash
cd /opt/school/dashboard && python -m pytest tests/test_calendar_import.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'school_dashboard.calendar_import'`

- [ ] **Step 2.4: Create calendar_import.py**

```bash
cat > /opt/school/dashboard/school_dashboard/calendar_import.py << 'EOF'
"""Parse SMCS school calendar PDF and seed school.db with events."""
import re
import sys
from typing import Optional

MONTH_MAP = {
    "JANUARY": "01", "FEBRUARY": "02", "MARCH": "03", "APRIL": "04",
    "MAY": "05", "JUNE": "06", "JULY": "07", "AUGUST": "08",
    "SEPTEMBER": "09", "OCTOBER": "10", "NOVEMBER": "11", "DECEMBER": "12",
}

# Spaced-letter month pattern: "A U G U S T"
MONTH_RE = re.compile(
    r"\b(" + "|".join(r"\s+".join(m) for m in MONTH_MAP) + r")\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(2\s*0\s*2\s*[5-9])\b")

SKIP_LINES = {
    "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT",
    "SUN MON TUE WED THU FRI SAT",
}

NO_SCHOOL_KEYWORDS = ["no school", "no school -", "no school–", "christmas eve",
                       "christmas day", "new year", "easter break", "thanksgiving"]
EARLY_RELEASE_KEYWORDS = ["early release", "12:25 dismissal", "dismissal"]
MASS_KEYWORDS = ["mass", "opening mass", "no mass"]
ASSEMBLY_KEYWORDS = ["assembly", "virtue assembly"]
PARENT_MTG_KEYWORDS = ["parent", "back to school night", "parent/teacher", "parent mtg",
                        "8th grade parent", "parent coffee"]
RETREAT_KEYWORDS = ["retreat", "confirmation"]
TESTING_KEYWORDS = ["map growth", "testing", "acre testing", "algebra/geometry exam",
                     "science fair", "placement test"]
CONCERT_KEYWORDS = ["concert", "program", "performance", "spring concert", "christmas program"]
SPORTS_KEYWORDS = ["game", "basketball", "field day", "clap out", "graduation"]
FIELD_TRIP_KEYWORDS = ["field trip"]
DANCE_KEYWORDS = ["dance", "mardi gras", "fall festival", "trunk or treat",
                   "open house", "job fair", "may crowning"]


def classify_event(text: str) -> str:
    lower = text.lower()
    if any(k in lower for k in NO_SCHOOL_KEYWORDS):
        return "NO_SCHOOL"
    if any(k in lower for k in EARLY_RELEASE_KEYWORDS):
        return "EARLY_RELEASE"
    if any(k in lower for k in ASSEMBLY_KEYWORDS):
        return "ASSEMBLY"
    if any(k in lower for k in PARENT_MTG_KEYWORDS):
        return "PARENT_MTG"
    if any(k in lower for k in RETREAT_KEYWORDS):
        return "RETREAT"
    if any(k in lower for k in TESTING_KEYWORDS):
        return "TESTING"
    if any(k in lower for k in CONCERT_KEYWORDS):
        return "CONCERT"
    if any(k in lower for k in SPORTS_KEYWORDS):
        return "SPORTS"
    if any(k in lower for k in FIELD_TRIP_KEYWORDS):
        return "FIELD_TRIP"
    if any(k in lower for k in MASS_KEYWORDS):
        return "MASS"
    if any(k in lower for k in DANCE_KEYWORDS):
        return "OTHER"
    return "OTHER"


def parse_month_header(text: str) -> Optional[tuple[str, str]]:
    """Extract (year, month_num) from a page containing spaced-letter month name."""
    month_match = MONTH_RE.search(text)
    year_match = YEAR_RE.search(text)
    if not month_match or not year_match:
        return None
    raw_month = re.sub(r"\s+", "", month_match.group(0)).upper()
    month_num = MONTH_MAP.get(raw_month)
    raw_year = re.sub(r"\s+", "", year_match.group(0))
    return (raw_year, month_num)


def parse_page_events(page_text: str) -> list[dict]:
    """Extract events from one calendar page. Returns list of {date, title, type}."""
    header = parse_month_header(page_text)
    if not header:
        return []
    year, month = header

    events = []
    # Split into lines, look for day numbers followed by event text
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]

    current_day = None
    current_text_parts = []

    def flush(day, parts):
        if day is None or not parts:
            return
        title = " ".join(parts).strip()
        # Normalize title
        title = re.sub(r"\s+", " ", title)
        if len(title) < 3:
            return
        event_date = f"{year}-{month}-{int(day):02d}"
        events.append({
            "date": event_date,
            "title": title,
            "type": classify_event(title),
        })

    for line in lines:
        # Skip header row
        if line in SKIP_LINES or re.match(r"^(SUN|MON|TUE|WED|THU|FRI|SAT)(\s+(SUN|MON|TUE|WED|THU|FRI|SAT))+$", line):
            continue
        # Skip spaced month/year lines
        if MONTH_RE.search(line) or re.match(r"^[A-Z]\s+[A-Z]\s+[A-Z]", line):
            continue
        if YEAR_RE.match(line.replace(" ", "")):
            continue

        # Check if line starts with a day number (1-31) possibly followed by text
        m = re.match(r"^(\d{1,2})\s*(.*)", line)
        if m:
            day_num = int(m.group(1))
            if 1 <= day_num <= 31:
                # Flush previous day
                flush(current_day, current_text_parts)
                current_day = str(day_num)
                current_text_parts = []
                rest = m.group(2).strip()
                if rest:
                    current_text_parts.append(rest)
                continue

        # Continuation text for current day
        if current_day and line:
            current_text_parts.append(line)

    flush(current_day, current_text_parts)
    return events


def import_calendar(pdf_path: str, db_path: str) -> int:
    """Parse PDF and insert all events into school.db. Returns count inserted."""
    try:
        import pypdf
    except ImportError:
        print("pypdf not installed. Run: pip install pypdf --break-system-packages", file=sys.stderr)
        return 0

    from school_dashboard.db import init_db, insert_event
    init_db(db_path)

    reader = pypdf.PdfReader(pdf_path)
    inserted = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        events = parse_page_events(text)
        for ev in events:
            if insert_event(db_path, ev["date"], ev["title"], ev["type"], source="calendar_pdf"):
                inserted += 1
    return inserted


if __name__ == "__main__":
    import os
    pdf = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SCHOOL_CALENDAR_PDF", "/opt/school/state/calendar.pdf")
    db = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SCHOOL_DB_PATH", "/opt/school/state/school.db")
    n = import_calendar(pdf, db)
    print(f"Imported {n} events into {db}")
EOF
```

- [ ] **Step 2.5: Run tests — verify they pass**

```bash
cd /opt/school/dashboard && python -m pytest tests/test_calendar_import.py -v
```

Expected: all tests PASS

- [ ] **Step 2.6: Run the import on the real PDF**

```bash
ssh root@192.168.1.14 "cd /opt/school/dashboard && \
  SCHOOL_DB_PATH=/opt/school/state/school.db \
  python -m school_dashboard.calendar_import \
    /opt/school/state/calendar.pdf /opt/school/state/school.db"
```

Expected: `Imported N events into /opt/school/state/school.db` (expect ~55–70)

- [ ] **Step 2.7: Spot-check the DB**

```bash
ssh root@192.168.1.14 "sqlite3 /opt/school/state/school.db \
  'SELECT date, title, type FROM events ORDER BY date LIMIT 20;'"
```

Expected: rows like `2025-08-21|Opening Mass|MASS`, `2025-09-01|NO SCHOOL - Labor Day|NO_SCHOOL`

- [ ] **Step 2.8: Commit**

```bash
cd /opt/school/dashboard && git add school_dashboard/calendar_import.py tests/test_calendar_import.py \
  && git commit -m "feat: parse SMCS calendar PDF into school.db (calendar_import.py)"
```

---

## Task 3: Email Intel Extractor

**Files:**
- Create: `/opt/school/dashboard/school_dashboard/intel.py`
- Create: `/opt/school/dashboard/tests/test_intel.py`

- [ ] **Step 3.1: Write failing tests**

```bash
cat > /opt/school/dashboard/tests/test_intel.py << 'EOF'
import json
import sys
from unittest.mock import patch, MagicMock
sys.path.insert(0, "/opt/school/dashboard")

from school_dashboard.intel import extract_from_email, process_digest


SAMPLE_EMAIL = {
    "id": "abc123",
    "subject": "Fall Festival – October 24",
    "from": "school@stmark.org",
    "snippet": "Join us Friday October 24 for the Fall Festival from 5-8pm. Volunteers needed.",
    "bucket": "SCHOOL",
    "attachments": [],
}

MOCK_LLM_RESPONSE = {
    "choices": [{
        "message": {
            "content": json.dumps({
                "events": [
                    {"date": "2025-10-24", "title": "Fall Festival", "type": "OTHER", "child": None, "notes": "5-8pm, volunteers needed"}
                ],
                "facts": [
                    {"subject": "general", "fact": "Fall Festival is October 24 5-8pm"}
                ]
            })
        }
    }]
}


def test_extract_from_email_returns_events_and_facts():
    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: MOCK_LLM_RESPONSE,
            raise_for_status=lambda: None,
        )
        result = extract_from_email(
            email=SAMPLE_EMAIL,
            litellm_url="https://llm.example.com",
            api_key="sk-test",
            model="claude-sonnet-4-6",
        )
    assert len(result["events"]) == 1
    assert result["events"][0]["title"] == "Fall Festival"
    assert len(result["facts"]) == 1


def test_extract_from_email_skips_skip_bucket():
    email = {**SAMPLE_EMAIL, "bucket": "SKIP"}
    result = extract_from_email(
        email=email,
        litellm_url="https://llm.example.com",
        api_key="sk-test",
        model="claude-sonnet-4-6",
    )
    assert result == {"events": [], "facts": []}


def test_extract_from_email_handles_llm_error():
    with patch("requests.post") as mock_post:
        mock_post.side_effect = Exception("Connection error")
        result = extract_from_email(
            email=SAMPLE_EMAIL,
            litellm_url="https://llm.example.com",
            api_key="sk-test",
            model="claude-sonnet-4-6",
        )
    assert result == {"events": [], "facts": []}


def test_process_digest_counts_insertions():
    digest = {"emails": [SAMPLE_EMAIL, {**SAMPLE_EMAIL, "id": "xyz", "bucket": "SKIP"}]}
    with patch("school_dashboard.intel.extract_from_email") as mock_extract, \
         patch("school_dashboard.intel.insert_event") as mock_insert, \
         patch("school_dashboard.intel.save_fact") as mock_save:
        mock_extract.return_value = {"events": [{"date": "2025-10-24", "title": "Fall Festival", "type": "OTHER", "child": None, "notes": None}], "facts": []}
        mock_insert.return_value = True
        result = process_digest(
            digest=digest,
            db_path=":memory:",
            facts_path="/tmp/facts_test.json",
            litellm_url="https://llm.example.com",
            api_key="sk-test",
            model="claude-sonnet-4-6",
        )
    # Only 1 email processed (SKIP bucket ignored)
    assert result["emails_processed"] == 1
    assert result["events_inserted"] == 1
EOF
```

- [ ] **Step 3.2: Run tests — verify they fail**

```bash
cd /opt/school/dashboard && python -m pytest tests/test_intel.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'school_dashboard.intel'`

- [ ] **Step 3.3: Create intel.py**

```bash
cat > /opt/school/dashboard/school_dashboard/intel.py << 'EOF'
"""Extract structured events and facts from classified emails via LiteLLM."""
import json
import logging
from typing import Optional

import requests

from school_dashboard.db import insert_event, save_fact

logger = logging.getLogger(__name__)

ACTIONABLE_BUCKETS = {"SCHOOL", "CHILD_ACTIVITY", "STARRED"}

EXTRACTION_SYSTEM = """You extract structured information from school emails for a family.
Family: Ford (2nd grade), Jack (7th grade), Penn (5th grade) — all at SMCS.

From the email, extract ONLY:
1. Calendar events: specific dates mentioned with what is happening
2. Recurring facts: schedules, contacts, or patterns worth remembering long-term

Return JSON only — no explanation, no markdown:
{
  "events": [{"date": "YYYY-MM-DD", "title": "short title", "type": "NO_SCHOOL|EARLY_RELEASE|MASS|ASSEMBLY|PARENT_MTG|RETREAT|TESTING|SPORTS|FIELD_TRIP|CONCERT|OTHER", "child": "ford|jack|penn|null", "notes": "optional detail"}],
  "facts": [{"subject": "ford|jack|penn|general", "fact": "one-line fact"}]
}
If nothing extractable, return: {"events": [], "facts": []}"""


def extract_from_email(
    email: dict,
    litellm_url: str,
    api_key: str,
    model: str,
) -> dict:
    """Call LiteLLM to extract events/facts from one email. Returns {events, facts}."""
    empty = {"events": [], "facts": []}

    if email.get("bucket") not in ACTIONABLE_BUCKETS:
        return empty

    subject = email.get("subject", "")
    snippet = email.get("snippet") or ""
    att_texts = []
    for att in email.get("attachments", []):
        if att.get("text_snippet"):
            att_texts.append(f"[Attachment: {att['filename']}]\n{att['text_snippet']}")

    content = f"Subject: {subject}\n\n{snippet}"
    if att_texts:
        content += "\n\n" + "\n\n".join(att_texts)

    try:
        resp = requests.post(
            f"{litellm_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": EXTRACTION_SYSTEM},
                    {"role": "user", "content": content},
                ],
                "max_tokens": 800,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Strip markdown code fences if present
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception as e:
        logger.warning("intel extract failed for email %s: %s", email.get("id"), e)
        return empty


def process_digest(
    digest: dict,
    db_path: str,
    facts_path: str,
    litellm_url: str,
    api_key: str,
    model: str,
) -> dict:
    """Process all emails in a digest, insert events/facts. Returns summary stats."""
    emails = digest.get("emails", [])
    emails_processed = 0
    events_inserted = 0
    facts_added = 0

    for email in emails:
        if email.get("bucket") not in ACTIONABLE_BUCKETS:
            continue
        result = extract_from_email(email, litellm_url, api_key, model)
        emails_processed += 1
        for ev in result.get("events", []):
            if insert_event(
                db_path,
                ev.get("date", ""),
                ev.get("title", ""),
                ev.get("type", "OTHER"),
                child=ev.get("child"),
                source="email",
                notes=ev.get("notes"),
            ):
                events_inserted += 1
        for fact in result.get("facts", []):
            if save_fact(facts_path, fact.get("subject", "general"), fact.get("fact", ""), source="email"):
                facts_added += 1

    return {
        "emails_processed": emails_processed,
        "events_inserted": events_inserted,
        "facts_added": facts_added,
    }
EOF
```

- [ ] **Step 3.4: Run tests — verify they pass**

```bash
cd /opt/school/dashboard && python -m pytest tests/test_intel.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 3.5: Commit**

```bash
cd /opt/school/dashboard && git add school_dashboard/intel.py tests/test_intel.py \
  && git commit -m "feat: LiteLLM email intel extractor (intel.py)"
```

---

## Task 4: Morning Digest Builder

**Files:**
- Create: `/opt/school/dashboard/school_dashboard/digest.py`
- Create: `/opt/school/dashboard/tests/test_digest.py`

- [ ] **Step 4.1: Write failing tests**

```bash
cat > /opt/school/dashboard/tests/test_digest.py << 'EOF'
import json
import sys
from unittest.mock import patch, MagicMock
sys.path.insert(0, "/opt/school/dashboard")

from school_dashboard.digest import build_digest_text, send_ntfy


SAMPLE_EVENTS = [
    {"date": "2026-04-13", "title": "No School – Easter Break", "type": "NO_SCHOOL", "child": None, "source": "calendar_pdf", "notes": None},
    {"date": "2026-04-14", "title": "No School – Easter Break", "type": "NO_SCHOOL", "child": None, "source": "calendar_pdf", "notes": None},
]

SAMPLE_STATE = {
    "children": {
        "Ford": {"ixl": {"minutes_today": 20, "goal_minutes": 30}},
        "Jack": {"ixl": {"minutes_today": 0, "goal_minutes": 30}},
        "Penn": {"ixl": {"minutes_today": 30, "goal_minutes": 30}},
    }
}

MOCK_LLM_RESPONSE = {
    "choices": [{"message": {"content": "📅 No school Mon-Fri this week (Easter break).\n📚 Jack: 0/30 IXL minutes."}}]
}


def test_build_digest_text_calls_litellm():
    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: MOCK_LLM_RESPONSE,
            raise_for_status=lambda: None,
        )
        text = build_digest_text(
            events=SAMPLE_EVENTS,
            state=SAMPLE_STATE,
            facts=[],
            litellm_url="https://llm.example.com",
            api_key="sk-test",
            model="claude-sonnet-4-6",
            system_prompt="You are a school digest assistant.",
        )
    assert "Easter" in text or "No school" in text or "IXL" in text


def test_build_digest_text_returns_fallback_on_error():
    with patch("requests.post") as mock_post:
        mock_post.side_effect = Exception("timeout")
        text = build_digest_text(
            events=SAMPLE_EVENTS,
            state=SAMPLE_STATE,
            facts=[],
            litellm_url="https://llm.example.com",
            api_key="sk-test",
            model="claude-sonnet-4-6",
            system_prompt="",
        )
    assert "No School" in text or "error" in text.lower()


def test_send_ntfy_posts_with_headers():
    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        send_ntfy(
            topic="test-topic",
            message="Hello",
            email="bryn@beary.us",
        )
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
        # Headers may be in kwargs
        _, kwargs = call_kwargs
        assert "bryn@beary.us" in str(kwargs)
EOF
```

- [ ] **Step 4.2: Run tests — verify they fail**

```bash
cd /opt/school/dashboard && python -m pytest tests/test_digest.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'school_dashboard.digest'`

- [ ] **Step 4.3: Create digest.py**

```bash
cat > /opt/school/dashboard/school_dashboard/digest.py << 'EOF'
"""Build and send the morning school digest."""
import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """You are a concise school digest assistant for the Beary family.
Kids: Ford (2nd grade), Jack (7th grade), Penn (5th grade) at SMCS.
Write a short morning briefing (under 1200 chars). Be specific. Skip empty sections.
Use these sections only if non-empty:
📅 THIS WEEK – upcoming school events / no-school days / early releases
⚡ ACTION NEEDED – deadlines, forms, replies required (note which child)
📚 IXL – flag any child with 0 minutes yesterday
🏫 FROM EMAIL – notable school updates
Terse. One line per item."""


def build_digest_text(
    events: list[dict],
    state: dict,
    facts: list[dict],
    litellm_url: str,
    api_key: str,
    model: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> str:
    """Call LiteLLM to synthesize a morning digest. Falls back to plain text on error."""
    today = date.today().isoformat()

    events_text = "\n".join(
        f"- {e['date']}: {e['title']}" + (f" ({e['child']})" if e.get("child") else "")
        for e in events
    ) or "No upcoming events."

    # Extract IXL summary from state
    ixl_lines = []
    for child, data in (state.get("children") or {}).items():
        ixl = data.get("ixl") or {}
        mins = ixl.get("minutes_today", 0)
        goal = ixl.get("goal_minutes", 30)
        ixl_lines.append(f"{child}: {mins}/{goal} min")
    ixl_text = ", ".join(ixl_lines) or "No IXL data."

    facts_text = "\n".join(f"- [{f['subject']}] {f['fact']}" for f in facts[:10]) or ""

    user_content = f"""Today: {today}

UPCOMING EVENTS (next 7 days):
{events_text}

IXL YESTERDAY:
{ixl_text}

KNOWN FACTS:
{facts_text}

Write the morning briefing now."""

    try:
        resp = requests.post(
            f"{litellm_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": 600,
            },
            timeout=45,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("digest LLM call failed: %s", e)
        # Plain-text fallback
        lines = [f"School digest – {today} (LLM error: {e})"]
        for ev in events:
            lines.append(f"• {ev['date']}: {ev['title']}")
        return "\n".join(lines)


def send_ntfy(topic: str, message: str, email: Optional[str] = None) -> bool:
    """POST to ntfy.sh. Optionally delivers email copy via Email header."""
    headers = {
        "Title": "📚 School Morning Digest",
        "Priority": "default",
        "Tags": "school,books",
    }
    if email:
        headers["Email"] = email

    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("ntfy send failed: %s", e)
        return False
EOF
```

- [ ] **Step 4.4: Run tests — verify they pass**

```bash
cd /opt/school/dashboard && python -m pytest tests/test_digest.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 4.5: Commit**

```bash
cd /opt/school/dashboard && git add school_dashboard/digest.py tests/test_digest.py \
  && git commit -m "feat: morning digest builder and ntfy sender (digest.py)"
```

---

## Task 5: Wire Everything into school-sync.sh

**Files:**
- Modify: `/opt/school/config/env`
- Modify: `/opt/school/dashboard/school-sync.sh`
- Modify: `/opt/school/dashboard/cron-prompts/morning-briefing.md`

- [ ] **Step 5.1: Add missing env vars**

```bash
ssh root@192.168.1.14 "cat >> /opt/school/config/env << 'EOF'

# Email + memory intelligence layer (added 2026-04-09)
SCHOOL_EMAIL_ACCOUNT=jd@beary.us
SCHOOL_DB_PATH=/opt/school/state/school.db
SCHOOL_FACTS_PATH=/opt/school/state/facts.json
SCHOOL_CALENDAR_PDF=/opt/school/state/calendar.pdf
BRYN_EMAIL=bryn@beary.us
EOF"
```

- [ ] **Step 5.2: Verify env vars are set**

```bash
ssh root@192.168.1.14 "grep -E 'SCHOOL_EMAIL_ACCOUNT|SCHOOL_DB_PATH|BRYN_EMAIL' /opt/school/config/env"
```

Expected: 3 lines printed

- [ ] **Step 5.3: Update school-sync.sh**

Replace the full file on the server:

```bash
ssh root@192.168.1.14 "cat > /opt/school/dashboard/school-sync.sh << 'SCRIPT'
#!/usr/bin/env bash
# school-sync.sh — Data refresh: scrape all sources, update state, regenerate dashboard.
# Runs via system cron at 6:00am and 2:30pm weekdays.
#
# Crontab:
#   0 6    * * 1-5  /opt/school/dashboard/school-sync.sh 2>>/tmp/school-sync.log
#   30 14  * * 1-5  /opt/school/dashboard/school-sync.sh 2>>/tmp/school-sync.log

set -euo pipefail

ENVFILE=\"\${SCHOOL_DASHBOARD_ENV:-/opt/school/config/env}\"
[[ -f \"\$ENVFILE\" ]] && { set -a; source \"\$ENVFILE\"; set +a; }

IXL_CRON=\"\${IXL_CRON:-/opt/school/ixl/cron/ixl-cron.sh}\"
IXL_DIR=\"\${IXL_DIR:-/tmp/ixl}\"
SGY_FILE=\"\${SGY_FILE:-/tmp/schoology-daily.json}\"

log() { echo \"\$(date '+%H:%M:%S') \$*\" >&2; }

# --- Step 1: IXL scrape ---
if [[ -x \"\$IXL_CRON\" ]]; then
    log \"Running IXL scrape...\"
    bash \"\$IXL_CRON\" || log \"WARN: IXL scrape had errors\"
else
    log \"WARN: IXL cron not found at \$IXL_CRON — skipping\"
fi

# --- Step 2: Schoology scrape ---
if command -v sgy &>/dev/null; then
    log \"Running Schoology scrape...\"
    sgy summary --json > \"\$SGY_FILE\" 2>/dev/null || log \"WARN: SGY scrape had errors\"
else
    log \"WARN: sgy not found — skipping\"
fi

# --- Step 3: Merge into state ---
log \"Updating state...\"
school-state update --ixl-dir \"\$IXL_DIR\" --sgy-file \"\$SGY_FILE\"

# --- Step 4: Email sync ---
if [[ -n \"\${SCHOOL_EMAIL_ACCOUNT:-}\" ]]; then
    log \"Syncing emails...\"
    school-state email-sync --account \"\$SCHOOL_EMAIL_ACCOUNT\" || log \"WARN: Email sync had errors\"
else
    log \"WARN: SCHOOL_EMAIL_ACCOUNT not set — skipping email sync\"
fi

# --- Step 5: Email intel (extract events + facts via LiteLLM) ---
if [[ -n \"\${SCHOOL_EMAIL_ACCOUNT:-}\" && -n \"\${SCHOOL_DB_PATH:-}\" ]]; then
    log \"Running email intel extraction...\"
    python3 -c \"
import json, os, sys
sys.path.insert(0, '/opt/school/dashboard')
from school_dashboard.intel import process_digest
from school_dashboard.db import init_db
db = os.environ.get('SCHOOL_DB_PATH', '/opt/school/state/school.db')
facts = os.environ.get('SCHOOL_FACTS_PATH', '/opt/school/state/facts.json')
digest_path = os.environ.get('SCHOOL_EMAIL_DIGEST', '/opt/school/state/email-digest.json')
init_db(db)
try:
    digest = json.loads(open(digest_path).read())
    result = process_digest(
        digest=digest,
        db_path=db,
        facts_path=facts,
        litellm_url=os.environ.get('LITELLM_URL', ''),
        api_key=os.environ.get('LITELLM_API_KEY', ''),
        model=os.environ.get('LITELLM_MODEL', 'claude-sonnet-4-6'),
    )
    print(f'Intel: {result}', file=sys.stderr)
except Exception as e:
    print(f'WARN: intel extraction failed: {e}', file=sys.stderr)
\" || log \"WARN: Email intel step had errors\"
fi

# --- Step 6: Regenerate dashboard ---
log \"Regenerating dashboard...\"
school-state html

log \"Sync complete.\"

# --- Step 7: Morning digest (6am run only) ---
HOUR=\$(date +%H)
if [[ \"\$HOUR\" -lt 10 ]]; then
    log \"Building morning digest...\"
    python3 -c \"
import json, os, sys
from datetime import date
sys.path.insert(0, '/opt/school/dashboard')
from school_dashboard.db import query_upcoming_events, load_facts
from school_dashboard.digest import build_digest_text, send_ntfy

db = os.environ.get('SCHOOL_DB_PATH', '/opt/school/state/school.db')
facts_path = os.environ.get('SCHOOL_FACTS_PATH', '/opt/school/state/facts.json')
state_path = os.environ.get('SCHOOL_STATE_PATH', '/opt/school/state/school-state.json')
topic = os.environ.get('NTFY_TOPIC', '')
bryn_email = os.environ.get('BRYN_EMAIL', '')
prompt_path = '/opt/school/dashboard/cron-prompts/morning-briefing.md'

events = query_upcoming_events(db, from_date=date.today().isoformat(), days=7)
facts = load_facts(facts_path)
try:
    state = json.loads(open(state_path).read())
except Exception:
    state = {}
try:
    system_prompt = open(prompt_path).read()
except Exception:
    system_prompt = ''

text = build_digest_text(
    events=events, state=state, facts=facts,
    litellm_url=os.environ.get('LITELLM_URL', ''),
    api_key=os.environ.get('LITELLM_API_KEY', ''),
    model=os.environ.get('LITELLM_MODEL', 'claude-sonnet-4-6'),
    system_prompt=system_prompt,
)
if topic:
    ok = send_ntfy(topic, text, email=bryn_email or None)
    print(f'ntfy sent: {ok}', file=sys.stderr)
else:
    print('WARN: NTFY_TOPIC not set', file=sys.stderr)
print(text, file=sys.stderr)
\" || log \"WARN: Morning digest had errors\"
fi
SCRIPT
chmod +x /opt/school/dashboard/school-sync.sh"
```

- [ ] **Step 5.4: Update morning-briefing.md** (replace openclaw placeholders)

```bash
ssh root@192.168.1.14 "cat > /opt/school/dashboard/cron-prompts/morning-briefing.md << 'EOF'
# Morning Briefing — System Prompt

You are a concise school digest assistant for the Beary family.
Kids: Ford (2nd grade), Jack (7th grade), Penn (5th grade) at SMCS (St. Margaret Catholic School).
Parents: JD and Bryn. Today's date is provided in the user message.

Write a short morning briefing under 1200 characters. Be specific, not generic. Skip empty sections entirely.

Format:
📅 THIS WEEK — upcoming school events, no-school days, early releases (12:25 dismissal)
⚡ ACTION NEEDED — anything requiring a parent response, signature, or deadline (note child)
📚 IXL — flag any child with 0 minutes yesterday; celebrate if all kids hit goal
🏫 FROM EMAIL — notable school updates or activity news

Rules:
- One line per item, terse
- Early releases: always note 12:25 dismissal
- No School days: note which days so parents can plan coverage
- If nothing for a section, omit the section header entirely
- End with a one-line summary: "X events this week."
EOF"
```

- [ ] **Step 5.5: Test the sync script dry-run**

```bash
ssh root@192.168.1.14 "bash -n /opt/school/dashboard/school-sync.sh && echo 'syntax OK'"
```

Expected: `syntax OK`

- [ ] **Step 5.6: Run a manual sync and watch logs**

```bash
ssh root@192.168.1.14 "bash /opt/school/dashboard/school-sync.sh 2>&1"
```

Verify: no unhandled errors; email sync runs; intel step runs; HTML regenerated.

- [ ] **Step 5.7: Force a morning digest test** (regardless of hour)

```bash
ssh root@192.168.1.14 "source /opt/school/config/env && python3 -c \"
import json, sys
from datetime import date
sys.path.insert(0, '/opt/school/dashboard')
from school_dashboard.db import query_upcoming_events, load_facts
from school_dashboard.digest import build_digest_text, send_ntfy

events = query_upcoming_events('/opt/school/state/school.db', from_date=date.today().isoformat(), days=7)
facts = load_facts('/opt/school/state/facts.json')
state = json.loads(open('/opt/school/state/school-state.json').read())
system_prompt = open('/opt/school/dashboard/cron-prompts/morning-briefing.md').read()
import os
text = build_digest_text(
    events=events, state=state, facts=facts,
    litellm_url=os.environ['LITELLM_URL'],
    api_key=os.environ['LITELLM_API_KEY'],
    model=os.environ['LITELLM_MODEL'],
    system_prompt=system_prompt,
)
print(text)
ok = send_ntfy(os.environ['NTFY_TOPIC'], text, email=os.environ.get('BRYN_EMAIL'))
print(f'Sent: {ok}')
\""
```

Expected: digest text printed, `Sent: True`

- [ ] **Step 5.8: Commit**

```bash
# On local dev machine (the sync.sh is on the server, commit local config/plan changes)
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard \
  && git add docs/superpowers/plans/ \
  && git commit -m "docs: add email/calendar/memory implementation plan"
```

---

## Task 6: Expand Chat Context in app.py

**Files:**
- Modify: `/opt/school/web/app.py` — add events + facts to `build_system_prompt()`

- [ ] **Step 6.1: Update build_system_prompt() on the server**

```bash
ssh root@192.168.1.14 "cat > /opt/school/web/app.py << 'PYEOF'
import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
import requests
from flask import Flask, jsonify, render_template, request, Response

app = Flask(__name__)

LITELLM_URL = os.environ.get(\"LITELLM_URL\", \"https://llm.grepon.cc\")
LITELLM_API_KEY = os.environ.get(\"LITELLM_API_KEY\", \"\")
LITELLM_MODEL = os.environ.get(\"LITELLM_MODEL\", \"claude-sonnet-4-6\")
STATE_PATH = os.environ.get(\"SCHOOL_STATE_PATH\", \"/opt/school/state/school-state.json\")
EMAIL_DIGEST_PATH = os.environ.get(\"SCHOOL_EMAIL_DIGEST\", \"/opt/school/state/email-digest.json\")
DB_PATH = os.environ.get(\"SCHOOL_DB_PATH\", \"/opt/school/state/school.db\")
FACTS_PATH = os.environ.get(\"SCHOOL_FACTS_PATH\", \"/opt/school/state/facts.json\")
DASHBOARD_HTML = \"/opt/school/state/school-dashboard.html\"


def load_json(path: str) -> dict | list:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {\"error\": f\"could not load {path}: {e}\"}


def load_upcoming_events(days: int = 30) -> list[dict]:
    if not Path(DB_PATH).exists():
        return []
    try:
        from_date = date.today().isoformat()
        end_date = (date.today() + timedelta(days=days)).isoformat()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            \"SELECT date, title, type, child FROM events WHERE date >= ? AND date < ? ORDER BY date\",
            (from_date, end_date),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def load_facts() -> list[dict]:
    try:
        p = Path(FACTS_PATH)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return []


def build_system_prompt() -> str:
    state = load_json(STATE_PATH)
    emails = load_json(EMAIL_DIGEST_PATH)
    events = load_upcoming_events(days=30)
    facts = load_facts()

    state_str = json.dumps(state, indent=2)[:8000]

    actionable = []
    if isinstance(emails, dict):
        items = emails.get(\"actionable\") or emails.get(\"emails\") or []
        if isinstance(items, list):
            actionable = [e for e in items if e.get(\"bucket\") not in (\"SKIP\", \"UNKNOWN\")][:20]
    emails_str = json.dumps(actionable, indent=2)[:3000]

    events_str = \"\\n\".join(
        f\"- {e['date']}: {e['title']}\" + (f\" ({e['child']})\" if e.get(\"child\") else \"\")
        for e in events
    ) or \"No upcoming events in DB.\"

    facts_str = \"\\n\".join(
        f\"- [{f.get('subject','?')}] {f.get('fact','')}\"
        for f in facts[:20]
    ) or \"No facts recorded yet.\"

    today = date.today().strftime(\"%A, %B %d, %Y\")

    return f\"\"\"You are a helpful family assistant for the Beary family school dashboard.

Family: Ford (2nd grade), Jack (7th grade), Penn (5th grade) — all at SMCS.
Today: {today}

Answer questions about grades, assignments, upcoming school events, and what needs attention. Be concise and practical.

=== UPCOMING SCHOOL EVENTS (next 30 days) ===
{events_str}

=== KNOWN FACTS ===
{facts_str}

=== SCHOOL STATE (grades, IXL, assignments) ===
{state_str}

=== ACTIONABLE EMAILS ===
{emails_str}
\"\"\"


@app.route(\"/\")
def index():
    return render_template(\"index.html\")


@app.route(\"/dashboard-frame\")
def dashboard_frame():
    try:
        with open(DASHBOARD_HTML) as f:
            return Response(f.read(), mimetype=\"text/html\")
    except Exception as e:
        return Response(
            f\"<html><body><h1>Dashboard not available</h1><p>{e}</p></body></html>\",
            mimetype=\"text/html\", status=500,
        )


@app.route(\"/api/chat\", methods=[\"POST\"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get(\"message\") or \"\").strip()
    history = data.get(\"history\") or []
    if not message:
        return jsonify({\"error\": \"empty message\"}), 400

    messages = [{\"role\": \"system\", \"content\": build_system_prompt()}]
    for h in history[-10:]:
        role = h.get(\"role\")
        content = h.get(\"content\", \"\")
        if role in (\"user\", \"assistant\") and content:
            messages.append({\"role\": role, \"content\": content})
    messages.append({\"role\": \"user\", \"content\": message})

    try:
        resp = requests.post(
            f\"{LITELLM_URL.rstrip('/')}/v1/chat/completions\",
            headers={\"Authorization\": f\"Bearer {LITELLM_API_KEY}\", \"Content-Type\": \"application/json\"},
            json={\"model\": LITELLM_MODEL, \"messages\": messages, \"max_tokens\": 1500},
            timeout=60,
        )
        resp.raise_for_status()
        reply = resp.json()[\"choices\"][0][\"message\"][\"content\"]
        return jsonify({\"reply\": reply})
    except Exception as e:
        return jsonify({\"error\": str(e)}), 500


if __name__ == \"__main__\":
    app.run(host=\"0.0.0.0\", port=5000)
PYEOF"
```

- [ ] **Step 6.2: Restart the web service**

```bash
ssh root@192.168.1.14 "systemctl restart school-web && sleep 2 && systemctl is-active school-web"
```

Expected: `active`

- [ ] **Step 6.3: Smoke test the chat endpoint**

```bash
ssh root@192.168.1.14 "curl -s -X POST http://localhost:5000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{\"message\": \"When is the next no-school day?\"}' | python3 -m json.tool"
```

Expected: JSON with `reply` field mentioning a specific date from school.db

- [ ] **Step 6.4: Commit**

```bash
# Sync the updated app.py back to local repo for tracking
scp root@192.168.1.14:/opt/school/web/app.py \
    /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard/web-app.py.bak
# (server is the source of truth for /opt/school/web/app.py)
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard \
  && git add -A && git commit -m "feat: expand chat context with 30-day events and facts"
```

---

## Self-Review Checklist

- [x] **calendar-import.py** → Task 2 ✓
- [x] **email-intel.py** → Task 3 ✓
- [x] **digest.py** → Task 4 ✓
- [x] **school-sync.sh** updated → Task 5 ✓
- [x] **app.py** expanded → Task 6 ✓
- [x] **env vars** added → Task 5.1 ✓
- [x] **SCHOOL_EMAIL_ACCOUNT** wired → Task 5.1 + sync.sh Step 4 ✓
- [x] **ntfy Email header** for Bryn → Task 4, `send_ntfy()` ✓
- [x] **morning-briefing.md** updated → Task 5.4 ✓
- [x] All test steps include exact commands and expected output ✓
- [x] No "TBD" or placeholder steps ✓
- [x] Function names consistent across tasks (`query_upcoming_events`, `load_facts`, `save_fact`, `insert_event`, `build_digest_text`, `send_ntfy`, `process_digest`, `extract_from_email`) ✓
