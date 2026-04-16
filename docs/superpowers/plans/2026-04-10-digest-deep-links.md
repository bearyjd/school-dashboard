# Context-Aware Digest Deep Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a parent taps an ntfy notification, open a full-screen card carousel showing exactly the items referenced in that digest.

**Architecture:** Digest builders return `(text, cards)` tuples. Cards are stored in a SQLite `digests` table with a short hex ID. ntfy deep link points to `?digest=<id>`. Dashboard detects this param and renders a swipeable CSS scroll-snap carousel with per-card mark-done checkboxes.

**Tech Stack:** Python 3.12, Flask, SQLite, vanilla JS, CSS scroll-snap.

**Spec:** `docs/superpowers/specs/2026-04-10-digest-deep-links-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `school_dashboard/db.py` | Modify | Add `digests` table schema + CRUD functions |
| `school_dashboard/digest.py` | Modify | Builders return `(text, cards)`, `send_ntfy` accepts cards |
| `school_dashboard/cli.py` | Modify | Unpack `(text, cards)` in `cmd_digest`, pass to `send_ntfy` |
| `web/app.py` | Modify | Add `GET /api/digest/<id>` and `PATCH /api/digest/<id>/cards/<int:index>` |
| `web/templates/index.html` | Modify | Add carousel rendering mode when `?digest=` param present |
| `tests/test_digest.py` | Modify | Update existing tests for `(text, cards)` return type, add new tests |

---

### Task 1: Digests Table — DB Layer

**Files:**
- Modify: `school_dashboard/db.py`
- Test: `tests/test_digest.py`

- [ ] **Step 1: Write failing tests for digest DB functions**

Add these tests to the bottom of `tests/test_digest.py`:

```python
# --- Digest DB tests ---

from school_dashboard.db import (
    create_digest,
    get_digest,
    mark_digest_card_done,
    purge_old_digests,
)


@pytest.fixture
def digest_db(tmp_path):
    """SQLite DB with digests table initialized."""
    db = tmp_path / "digest.db"
    from school_dashboard.db import init_digests_table
    init_digests_table(str(db))
    return str(db)


def test_create_and_get_digest(digest_db):
    cards = [
        {"source": "schoology", "child": "Ford", "title": "Math HW", "detail": "Pre-Algebra",
         "due_date": "2026-04-11", "url": "", "done": False},
        {"source": "ixl", "child": "Jack", "title": "Math", "detail": "3 remaining",
         "due_date": None, "url": "", "done": False},
    ]
    digest_id = create_digest(digest_db, "Morning Briefing", cards)
    assert len(digest_id) == 8
    result = get_digest(digest_db, digest_id)
    assert result is not None
    assert result["title"] == "Morning Briefing"
    assert len(result["cards"]) == 2
    assert result["cards"][0]["child"] == "Ford"
    assert result["cards"][1]["source"] == "ixl"


def test_get_digest_not_found(digest_db):
    assert get_digest(digest_db, "nonexist") is None


def test_mark_digest_card_done(digest_db):
    cards = [
        {"source": "schoology", "child": "Ford", "title": "Math HW", "detail": "",
         "due_date": None, "url": "", "done": False},
    ]
    digest_id = create_digest(digest_db, "Test", cards)
    assert mark_digest_card_done(digest_db, digest_id, 0, True) is True
    result = get_digest(digest_db, digest_id)
    assert result["cards"][0]["done"] is True


def test_mark_digest_card_done_invalid_index(digest_db):
    cards = [{"source": "ixl", "child": "Ford", "title": "X", "detail": "",
              "due_date": None, "url": "", "done": False}]
    digest_id = create_digest(digest_db, "Test", cards)
    assert mark_digest_card_done(digest_db, digest_id, 99, True) is False


def test_purge_old_digests(digest_db):
    cards = [{"source": "ixl", "child": "Ford", "title": "X", "detail": "",
              "due_date": None, "url": "", "done": False}]
    digest_id = create_digest(digest_db, "Old", cards)
    # Manually backdate the row
    import sqlite3
    conn = sqlite3.connect(digest_db)
    conn.execute("UPDATE digests SET created_at = '2020-01-01T00:00:00'")
    conn.commit()
    conn.close()
    purge_old_digests(digest_db, days=7)
    assert get_digest(digest_db, digest_id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard && python -m pytest tests/test_digest.py::test_create_and_get_digest -v`
Expected: FAIL with `ImportError: cannot import name 'create_digest' from 'school_dashboard.db'`

- [ ] **Step 3: Implement digest DB functions**

Add to the bottom of `school_dashboard/db.py`:

```python
import json as _json
import os


def init_digests_table(db_path: str) -> None:
    """Create digests table if it doesn't exist."""
    conn = _connect(db_path)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS digests (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                cards TEXT NOT NULL
            )
        """)
    conn.close()


def create_digest(db_path: str, title: str, cards: list[dict]) -> str:
    """Insert a digest and return its 8-char hex ID."""
    digest_id = os.urandom(4).hex()
    conn = _connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO digests (id, created_at, title, cards) VALUES (?, datetime('now'), ?, ?)",
            (digest_id, title, _json.dumps(cards)),
        )
    conn.close()
    return digest_id


def get_digest(db_path: str, digest_id: str) -> Optional[dict]:
    """Return digest with parsed cards list, or None."""
    if not Path(db_path).exists():
        return None
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM digests WHERE id = ?", (digest_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["cards"] = _json.loads(d["cards"])
    return d


def mark_digest_card_done(db_path: str, digest_id: str, card_index: int, done: bool) -> bool:
    """Toggle done state on a specific card. Returns False if not found or index invalid."""
    conn = _connect(db_path)
    row = conn.execute("SELECT cards FROM digests WHERE id = ?", (digest_id,)).fetchone()
    if not row:
        conn.close()
        return False
    cards = _json.loads(row["cards"])
    if card_index < 0 or card_index >= len(cards):
        conn.close()
        return False
    cards[card_index]["done"] = done
    with conn:
        conn.execute("UPDATE digests SET cards = ? WHERE id = ?", (_json.dumps(cards), digest_id))
    conn.close()
    return True


def purge_old_digests(db_path: str, days: int = 7) -> int:
    """Delete digests older than `days`. Returns count deleted."""
    if not Path(db_path).exists():
        return 0
    conn = _connect(db_path)
    with conn:
        cursor = conn.execute(
            "DELETE FROM digests WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
    count = cursor.rowcount
    conn.close()
    return count
```

Note: `json` is already imported at the top of `db.py` — no, it's not. Add `import json as _json` and `import os` at the top of db.py alongside existing imports. The `_json` alias avoids shadowing if any local var uses `json`.

Actually, looking at db.py again, it only imports `sqlite3`, `datetime`, `Path`, `Optional`. Add `import json` and `import os` to the top. Use `json` directly (no alias needed — there's no conflict).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard && python -m pytest tests/test_digest.py -k "digest_db or create_and_get or mark_digest or purge_old" -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add school_dashboard/db.py tests/test_digest.py
git commit -m "feat: add digests table for storing notification card data"
```

---

### Task 2: Digest Builders Return Cards

**Files:**
- Modify: `school_dashboard/digest.py`
- Test: `tests/test_digest.py`

- [ ] **Step 1: Write failing test for morning digest returning cards**

Add to `tests/test_digest.py`:

```python
@patch("school_dashboard.digest.requests.post")
def test_morning_digest_returns_cards(mock_post, tmp_state, tmp_facts, tmp_db):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Good morning!"}}]},
    )
    text, cards = build_morning_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        gcal_events=[{"title": "Soccer", "start": "2026-04-10T16:00", "location": "Field"}],
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        today="2026-04-10",
    )
    assert text == "Good morning!"
    # Should have: 1 assignment (Math HW due 2026-04-10), 1 IXL (Math 3 remaining),
    # 1 action item (Return field trip form), 1 db event (Mass), 1 gcal event (Soccer)
    assert len(cards) == 5
    sources = {c["source"] for c in cards}
    assert "schoology" in sources
    assert "ixl" in sources
    assert "email" in sources
    assert "calendar" in sources
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard && python -m pytest tests/test_digest.py::test_morning_digest_returns_cards -v`
Expected: FAIL with `ValueError: too many values to unpack` (currently returns str, not tuple)

- [ ] **Step 3: Update `build_morning_digest` to return `(text, cards)`**

In `school_dashboard/digest.py`, modify `build_morning_digest` (lines 117-184). After the existing data collection (lines 132-136) and before building the prompt strings, build the cards list. Change the return statement.

Replace the return statement at line 184:

```python
    # Before the prompt= line, build cards:
    cards: list[dict] = []
    for a in assignments:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": a["due_date"], "url": "", "done": False})
    for i in ixl:
        cards.append({"source": "ixl", "child": i["child"], "title": i["subject"],
                       "detail": f"{i['remaining']} remaining", "due_date": None, "url": "", "done": False})
    for a in action_items:
        cards.append({"source": "email", "child": a["child"], "title": a["summary"],
                       "detail": "Email action item", "due_date": today, "url": "", "done": False})
    for e in db_events:
        cards.append({"source": "calendar", "child": e.get("child", ""), "title": e["title"],
                       "detail": e["type"], "due_date": today, "url": "", "done": False})
    for e in cal_events:
        cards.append({"source": "calendar", "child": "", "title": e["title"],
                       "detail": e.get("location", ""), "due_date": today, "url": "", "done": False})

    # Change return from:
    #     return _call_litellm(prompt, litellm_url, api_key, model)
    # to:
    return _call_litellm(prompt, litellm_url, api_key, model), cards
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard && python -m pytest tests/test_digest.py::test_morning_digest_returns_cards -v`
Expected: PASS

- [ ] **Step 5: Update existing morning digest test to unpack tuple**

The existing `test_build_morning_digest_calls_litellm` does `result = build_morning_digest(...)` then `assert "Good morning!" in result`. This will break because `result` is now a tuple. Fix it:

Change line 102-112 in `tests/test_digest.py`:

```python
    result, cards = build_morning_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        gcal_events=[],
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        today="2026-04-10",
    )
    assert "Good morning!" in result
    assert isinstance(cards, list)
    mock_post.assert_called_once()
```

- [ ] **Step 6: Update `build_afternoon_digest` to return `(text, cards)`**

In `school_dashboard/digest.py`, modify `build_afternoon_digest` (lines 187-244). Before the `text = _call_litellm(...)` line, build cards. Update the return.

Insert before `text = _call_litellm(prompt, ...)` (line 237):

```python
    cards: list[dict] = []
    for a in due_today:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": today, "url": "", "done": False})
    for a in due_tomorrow:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": tomorrow, "url": "", "done": False})
    for i in ixl:
        cards.append({"source": "ixl", "child": i["child"], "title": i["subject"],
                       "detail": f"{i['remaining']} remaining", "due_date": None, "url": "", "done": False})
    for a in action_items:
        cards.append({"source": "email", "child": a["child"], "title": a["summary"],
                       "detail": "Email action item", "due_date": today, "url": "", "done": False})
```

Change the return at line 244 from `return text` to `return text, cards`. The readiness checklist append block (lines 238-243) modifies `text` before return — keep that, just change the final `return text` to `return text, cards`.

- [ ] **Step 7: Update existing afternoon digest tests**

Fix `test_build_afternoon_digest_calls_litellm` (line 117):

```python
    result, cards = build_afternoon_digest(
        state_path=tmp_state,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        today="2026-04-10",
    )
    assert "Homework check!" in result
    assert isinstance(cards, list)
```

Fix `test_afternoon_digest_includes_checklist` (line 177):

```python
    result, cards = build_afternoon_digest(
        state_path=tmp_state_with_items,
        db_path=tmp_db,
        litellm_url="http://fake-llm",
        api_key="key",
        model="gpt-4",
    )
    assert "Homework check done." in result
    assert "Action items:" in result
    assert isinstance(cards, list)
```

- [ ] **Step 8: Update `build_night_digest` to return `(text, cards)`**

In `school_dashboard/digest.py`, modify `build_night_digest` (lines 247-312). Before `text = _call_litellm(...)` (line 306), build cards:

```python
    cards: list[dict] = []
    for a in assignments:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": tomorrow, "url": "", "done": False})
    for e in db_events:
        cards.append({"source": "calendar", "child": e.get("child", ""), "title": e["title"],
                       "detail": e["type"], "due_date": tomorrow, "url": "", "done": False})
    for e in cal_events:
        cards.append({"source": "calendar", "child": "", "title": e["title"],
                       "detail": e.get("location", ""), "due_date": tomorrow, "url": "", "done": False})
    for a in action_items:
        cards.append({"source": "email", "child": a["child"], "title": a["summary"],
                       "detail": "Email action item", "due_date": tomorrow, "url": "", "done": False})
```

Change the final `return text` (line 312) to `return text, cards`.

- [ ] **Step 9: Update existing night digest tests**

Fix `test_build_night_digest_calls_litellm` (line 132):

```python
    result, cards = build_night_digest(
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        gcal_events=[],
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
        tomorrow="2026-04-11",
    )
    assert "Ready for tomorrow!" in result
    assert isinstance(cards, list)
```

Fix `test_night_digest_includes_checklist` (line 196):

```python
    result, cards = build_night_digest(
        state_path=tmp_state_with_items,
        db_path=tmp_db,
        facts_path="/dev/null",
        gcal_events=[],
        litellm_url="http://fake-llm",
        api_key="key",
        model="gpt-4",
    )
    assert "Night prep ready." in result
    assert "Before bed" in result
    assert isinstance(cards, list)
```

- [ ] **Step 10: Update `build_weekly_digest` to return `(text, cards)`**

In `school_dashboard/digest.py`, modify `build_weekly_digest` (lines 315-398). Before the final `return _call_litellm(...)` (line 398), build cards:

```python
    cards: list[dict] = []
    for a in upcoming_assignments:
        cards.append({"source": "schoology", "child": a["child"], "title": a["title"],
                       "detail": a["course"], "due_date": a.get("due_date", "")[:10], "url": "", "done": False})
    for i in ixl:
        cards.append({"source": "ixl", "child": i["child"], "title": i["subject"],
                       "detail": f"{i['remaining']} remaining", "due_date": None, "url": "", "done": False})
    for e in upcoming_events:
        cards.append({"source": "calendar", "child": e.get("child", ""), "title": e["title"],
                       "detail": e["type"], "due_date": e["date"], "url": "", "done": False})

    return _call_litellm(prompt, litellm_url, api_key, model), cards
```

- [ ] **Step 11: Update existing weekly digest tests**

Fix `test_weekly_digest_friday_builds_text` (line 217):

```python
    result, cards = build_weekly_digest(
        mode="friday",
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
    )
    assert result == "Week in review!"
    assert isinstance(cards, list)
    mock_post.assert_called_once()
```

Fix `test_weekly_digest_sunday_builds_text` (line 235):

```python
    result, cards = build_weekly_digest(
        mode="sunday",
        state_path=tmp_state,
        db_path=tmp_db,
        facts_path=tmp_facts,
        litellm_url="http://localhost:4000",
        api_key="test-key",
        model="claude-sonnet",
    )
    assert result == "Week ahead!"
    assert isinstance(cards, list)
    mock_post.assert_called_once()
```

Fix `test_weekly_digest_empty_state` (line 254):

```python
        result, cards = build_weekly_digest(
            mode="friday",
            state_path=missing,
            db_path=tmp_db,
            facts_path=tmp_facts,
            litellm_url="http://localhost:4000",
            api_key="test-key",
            model="claude-sonnet",
        )
    assert isinstance(result, str)
    assert len(result) > 0
    assert isinstance(cards, list)
```

- [ ] **Step 12: Run all tests to verify**

Run: `cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard && python -m pytest tests/test_digest.py -v`
Expected: ALL PASSED

- [ ] **Step 13: Commit**

```bash
git add school_dashboard/digest.py tests/test_digest.py
git commit -m "feat: digest builders return (text, cards) tuples with structured item data"
```

---

### Task 3: send_ntfy With Cards + Digest Storage

**Files:**
- Modify: `school_dashboard/digest.py`
- Test: `tests/test_digest.py`

- [ ] **Step 1: Write failing test for send_ntfy with cards**

Add to `tests/test_digest.py`:

```python
@patch("school_dashboard.digest.requests.post")
def test_send_ntfy_with_cards_creates_digest(mock_post, tmp_path):
    mock_post.return_value = MagicMock(ok=True, status_code=200)
    db = tmp_path / "school.db"
    from school_dashboard.db import init_digests_table
    init_digests_table(str(db))

    cards = [
        {"source": "schoology", "child": "Ford", "title": "Math HW", "detail": "Pre-Algebra",
         "due_date": "2026-04-11", "url": "", "done": False},
    ]
    send_ntfy(topic="test-topic", message="Hello", title="Homework Check",
              cards=cards, db_path=str(db))

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    headers = call_kwargs[1]["headers"] if "headers" in call_kwargs[1] else call_kwargs.kwargs["headers"]
    # Click header should contain ?digest= URL
    assert "digest=" in headers["Click"]
    assert "school.grepon.cc" in headers["Click"]


@patch("school_dashboard.digest.requests.post")
def test_send_ntfy_without_cards_uses_static_links(mock_post):
    mock_post.return_value = MagicMock(ok=True, status_code=200)
    send_ntfy(topic="test-topic", message="Hello", title="Homework Check")
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    headers = call_kwargs[1]["headers"] if "headers" in call_kwargs[1] else call_kwargs.kwargs["headers"]
    # Should use static deep link
    assert "mode=schoology" in headers["Click"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard && python -m pytest tests/test_digest.py::test_send_ntfy_with_cards_creates_digest -v`
Expected: FAIL with `TypeError: send_ntfy() got an unexpected keyword argument 'cards'`

- [ ] **Step 3: Update `send_ntfy` to accept cards**

Replace the entire `send_ntfy` function in `school_dashboard/digest.py` (lines 414-431):

```python
def send_ntfy(
    topic: str,
    message: str,
    title: str = "School",
    cards: list[dict] | None = None,
    db_path: str | None = None,
) -> None:
    """Push message to ntfy.sh topic. If cards provided, store digest and deep-link to carousel."""
    from school_dashboard.db import init_digests_table, create_digest, purge_old_digests

    url = DASHBOARD_BASE
    if cards and db_path:
        init_digests_table(db_path)
        purge_old_digests(db_path, days=7)
        digest_id = create_digest(db_path, title, cards)
        url = f"{DASHBOARD_BASE}/?digest={digest_id}"
    else:
        deep = _DEEP_LINKS.get(title, "")
        url = f"{DASHBOARD_BASE}/{deep}" if deep else DASHBOARD_BASE

    resp = requests.post(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={
            "Title": title.encode("ascii", errors="replace").decode("ascii"),
            "Priority": "default",
            "Tags": "school",
            "Click": url,
            "Actions": f"view, Open Dashboard, {url}",
        },
        timeout=15,
    )
    if not resp.ok:
        _log.warning("ntfy delivery failed: %s %s", resp.status_code, resp.text[:200])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard && python -m pytest tests/test_digest.py -k "send_ntfy" -v`
Expected: ALL PASSED (including the original `test_send_ntfy`)

- [ ] **Step 5: Commit**

```bash
git add school_dashboard/digest.py tests/test_digest.py
git commit -m "feat: send_ntfy stores cards in digest table and deep-links to carousel"
```

---

### Task 4: CLI Caller Update

**Files:**
- Modify: `school_dashboard/cli.py`

- [ ] **Step 1: Update `cmd_digest` to unpack tuples and pass cards**

Replace the `cmd_digest` function body in `school_dashboard/cli.py` (lines 118-174):

```python
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
        )
        _digest.send_ntfy(topic=ntfy_topic, message=text, title="Night Prep",
                          cards=cards, db_path=db_path)

    else:
        raise ValueError(f"Unknown mode: {args.mode}")

    print(f"Digest sent [{args.mode}]: {len(cards)} cards, {text[:80]}...", file=sys.stderr)
```

- [ ] **Step 2: Run all tests to verify nothing broke**

Run: `cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard && python -m pytest tests/ -v`
Expected: ALL PASSED

- [ ] **Step 3: Commit**

```bash
git add school_dashboard/cli.py
git commit -m "feat: cmd_digest passes cards to send_ntfy for carousel deep links"
```

---

### Task 5: API Endpoints for Digest Retrieval

**Files:**
- Modify: `web/app.py`

- [ ] **Step 1: Add GET /api/digest/<id> endpoint**

Add after the `/api/readiness` route in `web/app.py` (after line 337):

```python
@app.route("/api/digest/<digest_id>")
def api_digest_get(digest_id):
    from school_dashboard.db import init_digests_table, get_digest
    try:
        init_digests_table(DB_PATH)
        result = get_digest(DB_PATH, digest_id)
        if result is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/digest/<digest_id>/cards/<int:index>", methods=["PATCH"])
def api_digest_card_update(digest_id, index):
    from school_dashboard.db import init_digests_table, mark_digest_card_done
    data = request.get_json(silent=True) or {}
    done = data.get("done")
    if done is None:
        return jsonify({"error": "done field required"}), 400
    try:
        init_digests_table(DB_PATH)
        ok = mark_digest_card_done(DB_PATH, digest_id, index, bool(done))
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 2: Run all tests**

Run: `cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard && python -m pytest tests/ -v`
Expected: ALL PASSED

- [ ] **Step 3: Commit**

```bash
git add web/app.py
git commit -m "feat: add GET/PATCH endpoints for digest carousel cards"
```

---

### Task 6: Frontend Carousel

**Files:**
- Modify: `web/templates/index.html`

This task modifies the frontend to detect `?digest=` URL param and render a full-screen card carousel. The carousel uses CSS scroll-snap for native swipe behavior.

- [ ] **Step 1: Add carousel CSS**

Add the following CSS inside the existing `<style>` block in `index.html`, near the other dashboard styles:

```css
/* --- Carousel mode --- */
.carousel-overlay{position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:9999;
  background:#f5f5f5;display:flex;flex-direction:column;}
.carousel-progress{height:4px;background:#e0e0e0;flex-shrink:0;}
.carousel-progress-fill{height:100%;background:#4caf50;transition:width .3s;}
.carousel-header{display:flex;justify-content:space-between;align-items:center;
  padding:12px 16px;flex-shrink:0;}
.carousel-header .progress-text{font-size:14px;color:#666;}
.carousel-header .carousel-close{background:none;border:none;font-size:24px;cursor:pointer;
  color:#666;padding:4px 8px;}
.carousel-track{flex:1;display:flex;overflow-x:scroll;scroll-snap-type:x mandatory;
  -webkit-overflow-scrolling:touch;scroll-behavior:smooth;}
.carousel-track::-webkit-scrollbar{display:none;}
.carousel-card{min-width:100vw;scroll-snap-align:start;display:flex;flex-direction:column;
  align-items:center;justify-content:center;padding:24px 20px;box-sizing:border-box;}
.carousel-card-inner{background:#fff;border-radius:16px;padding:32px 24px;
  max-width:400px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.1);
  display:flex;flex-direction:column;align-items:center;text-align:center;gap:16px;}
.carousel-badge{display:inline-block;padding:4px 12px;border-radius:12px;
  font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;}
.carousel-badge.schoology{background:#e3f2fd;color:#1565c0;}
.carousel-badge.ixl{background:#e8f5e9;color:#2e7d32;}
.carousel-badge.email{background:#fff3e0;color:#e65100;}
.carousel-badge.calendar{background:#f3e5f5;color:#7b1fa2;}
.carousel-badge.readiness{background:#fce4ec;color:#c62828;}
.carousel-child{font-size:14px;color:#888;font-weight:500;}
.carousel-title{font-size:22px;font-weight:700;color:#222;line-height:1.3;}
.carousel-detail{font-size:15px;color:#666;}
.carousel-due{font-size:13px;color:#999;}
.carousel-link{font-size:13px;color:#1976d2;text-decoration:none;}
.carousel-link:hover{text-decoration:underline;}
.carousel-check{width:56px;height:56px;border-radius:50%;border:3px solid #ccc;
  background:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;
  font-size:28px;color:transparent;transition:all .2s;}
.carousel-check.done{border-color:#4caf50;background:#4caf50;color:#fff;}
.carousel-card-inner.done{opacity:.5;}
.carousel-card-inner.done .carousel-title{text-decoration:line-through;}
.carousel-dots{display:flex;justify-content:center;gap:6px;padding:12px;flex-shrink:0;}
.carousel-dot{width:8px;height:8px;border-radius:50%;background:#ccc;transition:background .2s;}
.carousel-dot.active{background:#333;}
.carousel-nav{position:absolute;top:50%;transform:translateY(-50%);background:rgba(0,0,0,.1);
  border:none;border-radius:50%;width:40px;height:40px;cursor:pointer;font-size:20px;
  display:none;align-items:center;justify-content:center;}
@media(min-width:768px){.carousel-nav{display:flex;}}
.carousel-nav.prev{left:12px;}
.carousel-nav.next{right:12px;}
.carousel-footer{text-align:center;padding:8px 16px 20px;flex-shrink:0;}
.carousel-footer a{color:#1976d2;font-size:14px;text-decoration:none;}
.carousel-alldone{display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:16px;padding:40px;text-align:center;}
.carousel-alldone .checkmark{font-size:64px;color:#4caf50;}
.carousel-alldone h2{font-size:24px;color:#333;}
```

- [ ] **Step 2: Add carousel JavaScript**

Add the following JavaScript inside the existing `<script>` block, before the URL param parsing IIFE at the bottom:

```javascript
// --- Carousel mode ---
let _carouselDigest = null;

function initCarousel(digestId) {
  fetch('/api/digest/' + digestId)
    .then(r => { if (!r.ok) throw new Error('not found'); return r.json(); })
    .then(data => {
      _carouselDigest = data;
      renderCarousel();
    })
    .catch(() => {
      // Digest not found or expired — fall back to normal dashboard
      console.warn('Digest not found, showing normal dashboard');
    });
}

function renderCarousel() {
  const d = _carouselDigest;
  if (!d || !d.cards || d.cards.length === 0) return;

  const overlay = document.createElement('div');
  overlay.className = 'carousel-overlay';
  overlay.id = 'carousel-overlay';

  const doneCount = d.cards.filter(c => c.done).length;
  const total = d.cards.length;
  const pct = Math.round((doneCount / total) * 100);

  let html = '';
  // Progress bar
  html += '<div class="carousel-progress"><div class="carousel-progress-fill" id="carousel-pbar" style="width:' + pct + '%"></div></div>';
  // Header
  html += '<div class="carousel-header">';
  html += '<span class="progress-text" id="carousel-ptext">' + doneCount + ' of ' + total + ' done</span>';
  html += '<button class="carousel-close" onclick="closeCarousel()">&times;</button>';
  html += '</div>';
  // Track
  html += '<div class="carousel-track" id="carousel-track" style="position:relative;">';
  d.cards.forEach(function(card, i) {
    const doneClass = card.done ? ' done' : '';
    html += '<div class="carousel-card" data-index="' + i + '">';
    html += '<div class="carousel-card-inner' + doneClass + '" id="ccard-' + i + '">';
    html += '<span class="carousel-badge ' + card.source + '">' + card.source + '</span>';
    if (card.child) html += '<span class="carousel-child">' + card.child + '</span>';
    html += '<div class="carousel-title">' + escapeHtml(card.title) + '</div>';
    if (card.detail) html += '<div class="carousel-detail">' + escapeHtml(card.detail) + '</div>';
    if (card.due_date) html += '<div class="carousel-due">Due: ' + card.due_date + '</div>';
    if (card.url) html += '<a class="carousel-link" href="' + card.url + '" target="_blank">Open in Schoology &rarr;</a>';
    html += '<button class="carousel-check' + (card.done ? ' done' : '') + '" id="ccheck-' + i + '" onclick="toggleCarouselCard(' + i + ')">&#10003;</button>';
    html += '</div></div>';
  });
  html += '</div>';
  // Nav arrows
  html += '<button class="carousel-nav prev" onclick="carouselPrev()">&#8249;</button>';
  html += '<button class="carousel-nav next" onclick="carouselNext()">&#8250;</button>';
  // Dots
  html += '<div class="carousel-dots" id="carousel-dots">';
  d.cards.forEach(function(_, i) {
    html += '<span class="carousel-dot' + (i === 0 ? ' active' : '') + '" data-i="' + i + '"></span>';
  });
  html += '</div>';
  // Footer
  html += '<div class="carousel-footer"><a href="/">View Full Dashboard</a></div>';

  overlay.innerHTML = html;
  document.body.appendChild(overlay);

  // Scroll tracking for dots
  const track = document.getElementById('carousel-track');
  track.addEventListener('scroll', function() {
    const idx = Math.round(track.scrollLeft / track.clientWidth);
    document.querySelectorAll('.carousel-dot').forEach(function(dot, i) {
      dot.classList.toggle('active', i === idx);
    });
  });
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function toggleCarouselCard(i) {
  const d = _carouselDigest;
  const card = d.cards[i];
  const newDone = !card.done;
  // Optimistic update
  card.done = newDone;
  const inner = document.getElementById('ccard-' + i);
  const check = document.getElementById('ccheck-' + i);
  inner.classList.toggle('done', newDone);
  check.classList.toggle('done', newDone);
  updateCarouselProgress();

  fetch('/api/digest/' + d.id + '/cards/' + i, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({done: newDone})
  });

  // Auto-advance to next undone card after 500ms
  if (newDone) {
    setTimeout(function() {
      const nextUndone = d.cards.findIndex(function(c, j) { return j > i && !c.done; });
      if (nextUndone >= 0) carouselGoTo(nextUndone);
      else {
        // Check if all done
        if (d.cards.every(function(c) { return c.done; })) showAllDone();
      }
    }, 500);
  }
}

function updateCarouselProgress() {
  const d = _carouselDigest;
  const doneCount = d.cards.filter(function(c) { return c.done; }).length;
  const total = d.cards.length;
  const pct = Math.round((doneCount / total) * 100);
  const pbar = document.getElementById('carousel-pbar');
  const ptext = document.getElementById('carousel-ptext');
  if (pbar) pbar.style.width = pct + '%';
  if (ptext) ptext.textContent = doneCount + ' of ' + total + ' done';
}

function carouselGoTo(i) {
  const track = document.getElementById('carousel-track');
  track.scrollTo({left: i * track.clientWidth, behavior: 'smooth'});
}

function carouselPrev() {
  const track = document.getElementById('carousel-track');
  const idx = Math.round(track.scrollLeft / track.clientWidth);
  if (idx > 0) carouselGoTo(idx - 1);
}

function carouselNext() {
  const track = document.getElementById('carousel-track');
  const idx = Math.round(track.scrollLeft / track.clientWidth);
  if (idx < _carouselDigest.cards.length - 1) carouselGoTo(idx + 1);
}

function closeCarousel() {
  const overlay = document.getElementById('carousel-overlay');
  if (overlay) overlay.remove();
  // Clean URL
  history.replaceState(null, '', '/');
}

function showAllDone() {
  const track = document.getElementById('carousel-track');
  if (!track) return;
  const card = document.createElement('div');
  card.className = 'carousel-card';
  card.innerHTML = '<div class="carousel-alldone">' +
    '<div class="checkmark">&#10003;</div>' +
    '<h2>All caught up!</h2>' +
    '<a href="/" style="color:#1976d2;font-size:16px;">View Full Dashboard</a>' +
    '</div>';
  track.appendChild(card);
  setTimeout(function() { carouselGoTo(_carouselDigest.cards.length); }, 300);
}
```

- [ ] **Step 3: Add carousel init to URL param detection**

Update the URL param parsing IIFE at the bottom of the `<script>` block. Add digest detection BEFORE the existing mode/time/child parsing:

```javascript
(function(){
  const p=new URLSearchParams(window.location.search);
  // Carousel mode takes priority
  if(p.get('digest')){
    initCarousel(p.get('digest'));
    return; // Skip normal dashboard URL param handling
  }
  if(p.get('mode')){dashMode=p.get('mode');document.querySelectorAll('#dash-mode-bar .dtab').forEach(b=>b.classList.toggle('active',b.dataset.mode===dashMode));}
  if(p.get('time')){dashTime=p.get('time');document.querySelectorAll('#dash-time-bar .dtab').forEach(b=>b.classList.toggle('active',b.dataset.time===dashTime));}
  if(p.get('child')){dashChild=p.get('child');}
})();
```

- [ ] **Step 4: Deploy and manual test**

```bash
scp web/templates/index.html root@192.168.1.14:/opt/school/dashboard/web/templates/index.html
ssh root@192.168.1.14 'pkill -f flask; sleep 1; cd /opt/school/dashboard && set -a && source /opt/school/config/env && set +a && nohup python3 -m web.app > /tmp/flask.log 2>&1 &'
```

Then send a test notification:

```bash
ssh root@192.168.1.14 'cd /opt/school/dashboard && set -a && source /opt/school/config/env && set +a && python3 -c "
from school_dashboard.digest import send_ntfy
from school_dashboard.db import init_digests_table
import os
db = os.environ.get(\"SCHOOL_DB_PATH\", \"/opt/school/state/school.db\")
init_digests_table(db)
cards = [
    {\"source\": \"schoology\", \"child\": \"Ford\", \"title\": \"Math Chapter 5 Review\", \"detail\": \"Pre-Algebra\", \"due_date\": \"2026-04-11\", \"url\": \"\", \"done\": False},
    {\"source\": \"schoology\", \"child\": \"Ford\", \"title\": \"ELA Essay Draft\", \"detail\": \"English\", \"due_date\": \"2026-04-11\", \"url\": \"\", \"done\": False},
    {\"source\": \"ixl\", \"child\": \"Jack\", \"title\": \"Math\", \"detail\": \"3 remaining\", \"due_date\": None, \"url\": \"\", \"done\": False},
]
send_ntfy(os.environ[\"NTFY_TOPIC\"], \"Ford: 2 assignments due tomorrow. Jack: 3 IXL skills remaining.\", title=\"Homework Check\", cards=cards, db_path=db)
print(\"Sent with cards!\")
"'
```

Tap the notification. Verify:
- Carousel opens with 3 cards
- Swipe works between cards
- Tapping checkbox marks done and auto-advances
- Progress bar updates
- "All caught up!" shows when all done
- X and "View Full Dashboard" exit carousel

- [ ] **Step 5: Commit**

```bash
git add web/templates/index.html
git commit -m "feat: add card carousel UI for digest deep links"
```

---

### Task 7: Deploy All Backend Changes

**Files:**
- No code changes — deployment only

- [ ] **Step 1: Deploy updated backend files to server**

```bash
scp school_dashboard/db.py root@192.168.1.14:/opt/school/dashboard/school_dashboard/db.py
scp school_dashboard/digest.py root@192.168.1.14:/opt/school/dashboard/school_dashboard/digest.py
scp school_dashboard/cli.py root@192.168.1.14:/opt/school/dashboard/school_dashboard/cli.py
scp web/app.py root@192.168.1.14:/opt/school/dashboard/web/app.py
scp web/templates/index.html root@192.168.1.14:/opt/school/dashboard/web/templates/index.html
```

- [ ] **Step 2: Restart Flask**

```bash
ssh root@192.168.1.14 'pkill -f flask; sleep 1; cd /opt/school/dashboard && set -a && source /opt/school/config/env && set +a && nohup python3 -m web.app > /tmp/flask.log 2>&1 &'
```

- [ ] **Step 3: Send end-to-end test notification with real data**

```bash
ssh root@192.168.1.14 'cd /opt/school/dashboard && set -a && source /opt/school/config/env && set +a && python3 -c "
from school_dashboard.digest import send_ntfy
from school_dashboard.db import init_digests_table
import os
db = os.environ.get(\"SCHOOL_DB_PATH\", \"/opt/school/state/school.db\")
init_digests_table(db)
cards = [
    {\"source\": \"schoology\", \"child\": \"Ford\", \"title\": \"Math Ch5 Review\", \"detail\": \"Pre-Algebra\", \"due_date\": \"2026-04-11\", \"url\": \"\", \"done\": False},
    {\"source\": \"schoology\", \"child\": \"Ford\", \"title\": \"ELA Essay Draft\", \"detail\": \"English\", \"due_date\": \"2026-04-11\", \"url\": \"\", \"done\": False},
    {\"source\": \"schoology\", \"child\": \"Ford\", \"title\": \"Science Lab Report\", \"detail\": \"Science 7\", \"due_date\": \"2026-04-12\", \"url\": \"\", \"done\": False},
    {\"source\": \"ixl\", \"child\": \"Jack\", \"title\": \"Math\", \"detail\": \"3 remaining\", \"due_date\": None, \"url\": \"\", \"done\": False},
    {\"source\": \"ixl\", \"child\": \"Jack\", \"title\": \"ELA\", \"detail\": \"2 remaining\", \"due_date\": None, \"url\": \"\", \"done\": False},
]
send_ntfy(os.environ[\"NTFY_TOPIC\"], \"Ford: 3 assignments due. Jack: 5 IXL skills remaining.\", title=\"Homework Check\", cards=cards, db_path=db)
print(\"Sent 5-card test!\")
"'
```

Verify carousel works end-to-end on phone.
