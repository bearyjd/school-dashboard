# Mobile App — Plan 1: Backend Changes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-source sync metadata tracking, `/api/sync/meta` endpoint, data freshness in LLM system prompt, and `/.well-known/assetlinks.json` for TWA domain verification.

**Architecture:** New `school_dashboard/sync_meta.py` module handles reading/writing `state/sync_meta.json`. `web/app.py` gains three new routes and injects freshness into the chat system prompt. `sync/school-sync.sh` writes metadata after each scraper. All changes are TDD.

**Tech Stack:** Python 3.12, Flask, pytest, existing project patterns (load_json, env var path overrides)

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `school_dashboard/sync_meta.py` | Create | read/write sync_meta.json |
| `tests/test_sync_meta.py` | Create | unit tests for sync_meta module |
| `web/app.py` | Modify | `_run_sync_background`, `build_system_prompt`, 3 new routes, `_format_freshness` helper |
| `tests/test_sync.py` | Modify | add tests for `/api/sync/meta`, `/.well-known/assetlinks.json`, `_format_freshness` |
| `sync/school-sync.sh` | Modify | write sync_meta after each scraper step |
| `config/env.example` | Modify | add `SCHOOL_SYNC_META_PATH` and TWA vars |
| `.env.example` | Modify | same |
| `CLAUDE.md` | Modify | update state files table, config section, test count |

---

### Task 1: `school_dashboard/sync_meta.py` module

**Files:**
- Create: `school_dashboard/sync_meta.py`
- Create: `tests/test_sync_meta.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_sync_meta.py`:

```python
"""Tests for school_dashboard/sync_meta.py"""
import json
import pytest
from school_dashboard.sync_meta import read_sync_meta, write_sync_source


def test_read_missing_file_returns_empty(tmp_path):
    result = read_sync_meta(str(tmp_path / "nonexistent.json"))
    assert result == {}


def test_read_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "meta.json"
    p.write_text("not valid json")
    assert read_sync_meta(str(p)) == {}


def test_write_creates_file_with_source(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("ixl", "ok", path=p)
    meta = read_sync_meta(p)
    assert meta["ixl"]["last_result"] == "ok"
    assert "last_run" in meta["ixl"]


def test_write_updates_existing_source(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("ixl", "ok", path=p)
    write_sync_source("ixl", "error", path=p)
    meta = read_sync_meta(p)
    assert meta["ixl"]["last_result"] == "error"


def test_write_preserves_other_sources(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("ixl", "ok", path=p)
    write_sync_source("sgy", "ok", path=p)
    meta = read_sync_meta(p)
    assert "ixl" in meta
    assert "sgy" in meta


def test_write_creates_parent_dirs(tmp_path):
    p = str(tmp_path / "nested" / "dir" / "meta.json")
    write_sync_source("gc", "ok", path=p)
    meta = read_sync_meta(p)
    assert meta["gc"]["last_result"] == "ok"


def test_last_run_is_iso_timestamp(tmp_path):
    p = str(tmp_path / "meta.json")
    write_sync_source("ixl", "ok", path=p)
    meta = read_sync_meta(p)
    ts = meta["ixl"]["last_run"]
    # Should be parseable ISO format
    from datetime import datetime
    dt = datetime.fromisoformat(ts)
    assert dt.year >= 2026
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
python -m pytest tests/test_sync_meta.py -v
```

Expected: `ModuleNotFoundError: No module named 'school_dashboard.sync_meta'`

- [ ] **Step 3: Create `school_dashboard/sync_meta.py`**

```python
"""Per-source sync metadata: read/write state/sync_meta.json."""
import json
import os
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = "/app/state/sync_meta.json"


def _resolve_path(path: str | None) -> str:
    return path or os.environ.get("SCHOOL_SYNC_META_PATH", DEFAULT_PATH)


def read_sync_meta(path: str | None = None) -> dict:
    """Return per-source sync metadata dict. Returns {} if file missing or corrupt."""
    p = _resolve_path(path)
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_sync_source(source: str, result: str, path: str | None = None) -> None:
    """Write/update a single source entry in sync_meta.json."""
    p = _resolve_path(path)
    meta = read_sync_meta(p)
    meta[source] = {
        "last_run": datetime.utcnow().isoformat(timespec="seconds"),
        "last_result": result,
    }
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(meta, f, indent=2)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_sync_meta.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add school_dashboard/sync_meta.py tests/test_sync_meta.py
git commit -m "feat: sync_meta module — persist per-source last_run timestamps"
```

---

### Task 2: Write sync_meta from `_run_sync_background`

**Files:**
- Modify: `web/app.py` (`_run_sync_background` function, lines 383–471)
- Modify: `tests/test_sync.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_sync.py` (after the existing imports, add `MagicMock`):

```python
# At top of file, ensure these imports exist:
from unittest.mock import patch, MagicMock
```

Add this test at the bottom of `tests/test_sync.py`:

```python
@patch("web.app.subprocess.run")
def test_run_sync_background_writes_meta_on_success(mock_sub, tmp_path, monkeypatch):
    """_run_sync_background writes sync_meta.json after each source completes."""
    import web.app as app_module
    from school_dashboard.sync_meta import read_sync_meta

    meta_path = str(tmp_path / "meta.json")
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", meta_path)
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SCHOOL_DB_PATH", str(tmp_path / "db.db"))
    monkeypatch.setenv("IXL_DIR", str(tmp_path / "ixl"))
    monkeypatch.setenv("SGY_FILE", str(tmp_path / "sgy.json"))
    monkeypatch.setenv("IXL_CRON", "")

    mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")

    # _run_sync_background expects the lock to already be held
    acquired = app_module._sync_lock.acquire(blocking=False)
    assert acquired, "lock should be free before test"

    app_module._run_sync_background("ixl", "none")

    meta = read_sync_meta(meta_path)
    assert meta.get("ixl", {}).get("last_result") == "ok"


@patch("web.app.subprocess.run")
def test_run_sync_background_writes_meta_on_error(mock_sub, tmp_path, monkeypatch):
    """_run_sync_background writes error result when scraper raises."""
    import web.app as app_module
    from school_dashboard.sync_meta import read_sync_meta

    meta_path = str(tmp_path / "meta.json")
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", meta_path)
    monkeypatch.setenv("SCHOOL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SCHOOL_DB_PATH", str(tmp_path / "db.db"))
    monkeypatch.setenv("IXL_DIR", str(tmp_path / "ixl"))
    monkeypatch.setenv("SGY_FILE", str(tmp_path / "sgy.json"))
    monkeypatch.setenv("IXL_CRON", "")

    # First call (ixl scrape) raises, second call (state update) succeeds
    mock_sub.side_effect = [Exception("scraper failed"), MagicMock(returncode=0), MagicMock(returncode=0)]

    acquired = app_module._sync_lock.acquire(blocking=False)
    assert acquired

    app_module._run_sync_background("ixl", "none")

    meta = read_sync_meta(meta_path)
    assert meta.get("ixl", {}).get("last_result") == "error"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_sync.py::test_run_sync_background_writes_meta_on_success tests/test_sync.py::test_run_sync_background_writes_meta_on_error -v
```

Expected: tests fail (sync_meta not written yet)

- [ ] **Step 3: Update `_run_sync_background` in `web/app.py`**

Add import at top of `web/app.py` (after existing imports):

```python
from school_dashboard.sync_meta import write_sync_source
```

In `_run_sync_background`, replace the `for src in source_list:` block. The current code has a bare `except Exception as exc: errors.append(...)`. Change it so each source writes its result:

```python
    for src in source_list:
        try:
            if src == "ixl":
                if ixl_cron and Path(ixl_cron).exists():
                    subprocess.run(["bash", ixl_cron], timeout=120, check=False)
                else:
                    Path(ixl_dir).mkdir(parents=True, exist_ok=True)
                    result = subprocess.run(
                        ["ixl", "summary", "--json"],
                        capture_output=True, text=True, timeout=120,
                    )
                    if result.stdout:
                        (Path(ixl_dir) / "ixl-summary.json").write_text(result.stdout)
            elif src == "sgy":
                result = subprocess.run(
                    ["sgy", "summary", "--json"],
                    capture_output=True, text=True, timeout=60,
                )
                if result.stdout:
                    Path(sgy_file).write_text(result.stdout)
            elif src == "gc":
                gc_script = "/app/sync/gc-scrape.sh"
                if Path(gc_script).exists():
                    subprocess.run(["bash", gc_script], timeout=60, check=False)
            write_sync_source(src, "ok")
        except Exception as exc:
            errors.append(f"{src}: {exc}")
            write_sync_source(src, "error")
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_sync.py -v
```

Expected: all 10 tests pass (8 original + 2 new)

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_sync.py
git commit -m "feat: write sync_meta per source in _run_sync_background"
```

---

### Task 3: `GET /api/sync/meta` endpoint

**Files:**
- Modify: `web/app.py` (add route after `api_sync_status`)
- Modify: `tests/test_sync.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_sync.py`:

```python
def test_sync_meta_returns_empty_dict_when_no_file(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(tmp_path / "nonexistent.json"))
    r = client.get("/api/sync/meta")
    assert r.status_code == 200
    assert r.get_json() == {}


def test_sync_meta_returns_written_data(client, tmp_path, monkeypatch):
    from school_dashboard.sync_meta import write_sync_source
    p = str(tmp_path / "meta.json")
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", p)
    write_sync_source("ixl", "ok", path=p)
    r = client.get("/api/sync/meta")
    data = r.get_json()
    assert data["ixl"]["last_result"] == "ok"
    assert "last_run" in data["ixl"]
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_sync.py::test_sync_meta_returns_empty_dict_when_no_file tests/test_sync.py::test_sync_meta_returns_written_data -v
```

Expected: `404 NOT FOUND` (route not yet defined)

- [ ] **Step 3: Add route to `web/app.py`**

Add after the `api_sync_status` route (after line ~498):

```python
@app.route("/api/sync/meta")
def api_sync_meta():
    from school_dashboard.sync_meta import read_sync_meta
    meta_path = os.environ.get("SCHOOL_SYNC_META_PATH", "/app/state/sync_meta.json")
    return jsonify(read_sync_meta(meta_path))
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_sync.py -v
```

Expected: all 12 tests pass

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_sync.py
git commit -m "feat: GET /api/sync/meta — per-source sync freshness endpoint"
```

---

### Task 4: Data freshness in `build_system_prompt`

**Files:**
- Modify: `web/app.py` (add `_format_freshness` helper, update `build_system_prompt`)
- Modify: `tests/test_sync.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_sync.py`:

```python
def test_format_freshness_shows_never_for_missing_sources():
    from web.app import _format_freshness
    result = _format_freshness({})
    assert "IXL: never pulled" in result
    assert "SGY: never pulled" in result
    assert "GC: never pulled" in result


def test_format_freshness_shows_days_ago():
    from web.app import _format_freshness
    from datetime import datetime, timedelta
    old_ts = (datetime.utcnow() - timedelta(days=12)).isoformat(timespec="seconds")
    meta = {"ixl": {"last_run": old_ts, "last_result": "ok"}}
    result = _format_freshness(meta)
    assert "12 days ago" in result
    assert "IXL" in result


def test_format_freshness_shows_today_for_recent():
    from web.app import _format_freshness
    from datetime import datetime
    ts = datetime.utcnow().isoformat(timespec="seconds")
    meta = {"sgy": {"last_run": ts, "last_result": "ok"}}
    result = _format_freshness(meta)
    assert "today" in result or "just now" in result


def test_format_freshness_shows_yesterday():
    from web.app import _format_freshness
    from datetime import datetime, timedelta
    ts = (datetime.utcnow() - timedelta(days=1)).isoformat(timespec="seconds")
    meta = {"gc": {"last_run": ts, "last_result": "ok"}}
    result = _format_freshness(meta)
    assert "yesterday" in result
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_sync.py::test_format_freshness_shows_never_for_missing_sources tests/test_sync.py::test_format_freshness_shows_days_ago tests/test_sync.py::test_format_freshness_shows_today_for_recent tests/test_sync.py::test_format_freshness_shows_yesterday -v
```

Expected: `ImportError: cannot import name '_format_freshness' from 'web.app'`

- [ ] **Step 3: Add `_format_freshness` to `web/app.py`**

Add this function before `build_system_prompt`:

```python
def _format_freshness(meta: dict) -> str:
    """Format per-source sync freshness for LLM system prompt."""
    now = datetime.utcnow()
    lines = []
    for source in ("ixl", "sgy", "gc"):
        entry = meta.get(source)
        if not entry:
            lines.append(f"{source.upper()}: never pulled")
            continue
        try:
            last = datetime.fromisoformat(entry["last_run"])
            delta = now - last
            days = delta.days
            if days == 0:
                hours = delta.seconds // 3600
                age = f"today ({hours}h ago)" if hours > 0 else "just now"
            elif days == 1:
                age = "yesterday"
            else:
                age = f"{days} days ago"
        except (KeyError, ValueError):
            age = "unknown"
        result_str = entry.get("last_result", "?")
        lines.append(f"{source.upper()}: last pulled {age} — {result_str}")
    return "\n".join(lines)
```

- [ ] **Step 4: Update `build_system_prompt` to inject freshness**

Replace the existing `build_system_prompt` function in `web/app.py`:

```python
def build_system_prompt() -> str:
    from school_dashboard.sync_meta import read_sync_meta
    meta_path = os.environ.get("SCHOOL_SYNC_META_PATH", "/app/state/sync_meta.json")
    meta = read_sync_meta(meta_path)
    freshness_str = _format_freshness(meta)

    state = load_json(STATE_PATH)
    emails = load_json(EMAIL_DIGEST_PATH)
    events = load_upcoming_events(days=30)
    facts = load_facts()

    state_str = json.dumps(state, indent=2).replace('"John"', '"Jack"')[:8000]

    actionable = []
    if isinstance(emails, dict):
        items = emails.get("actionable") or emails.get("emails") or []
        if isinstance(items, list):
            actionable = [e for e in items if e.get("bucket") not in ("SKIP", "UNKNOWN")][:20]
    emails_str = json.dumps(actionable, indent=2)[:3000]

    events_str = "\n".join(
        "- " + e["date"] + ": " + e["title"] + (" (" + e["child"] + ")" if e.get("child") else "")
        for e in events
    ) or "No upcoming events in DB."

    facts_str = "\n".join(
        "- [" + f.get("subject", "?") + "] " + f.get("fact", "")
        for f in facts[:20]
    ) or "No facts recorded yet."

    today = date.today().strftime("%A, %B %d, %Y")

    return (
        "You are a helpful family assistant for the school dashboard.\n\n"
        "Answer questions about the children's grades, assignments, upcoming school events, and what needs attention.\n"
        f"Today: {today}\n\n"
        "Answer questions about grades, assignments, upcoming school events, and what needs attention. Be concise and practical.\n\n"
        f"## Data Freshness\n{freshness_str}\n\n"
        f"=== UPCOMING SCHOOL EVENTS (next 30 days) ===\n{events_str}\n\n"
        f"=== KNOWN FACTS ===\n{facts_str}\n\n"
        f"=== SCHOOL STATE (grades, IXL, assignments) ===\n{state_str}\n\n"
        f"=== ACTIONABLE EMAILS ===\n{emails_str}\n"
    )
```

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass (existing + 4 new freshness tests)

- [ ] **Step 6: Commit**

```bash
git add web/app.py tests/test_sync.py
git commit -m "feat: inject per-source data freshness into LLM system prompt"
```

---

### Task 5: `/.well-known/assetlinks.json` endpoint

**Files:**
- Modify: `web/app.py` (add route)
- Modify: `tests/test_sync.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_sync.py`:

```python
def test_assetlinks_returns_json_list(client):
    r = client.get("/.well-known/assetlinks.json")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, list)
    assert data[0]["relation"] == ["delegate_permission/common.handle_all_urls"]
    assert "namespace" in data[0]["target"]


def test_assetlinks_uses_env_package_name(client, monkeypatch):
    monkeypatch.setenv("TWA_PACKAGE_NAME", "com.example.myapp")
    r = client.get("/.well-known/assetlinks.json")
    data = r.get_json()
    assert data[0]["target"]["package_name"] == "com.example.myapp"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_sync.py::test_assetlinks_returns_json_list tests/test_sync.py::test_assetlinks_uses_env_package_name -v
```

Expected: `404 NOT FOUND`

- [ ] **Step 3: Add route to `web/app.py`**

Add after the `api_sync_meta` route:

```python
@app.route("/.well-known/assetlinks.json")
def assetlinks():
    """TWA domain verification — fingerprint populated when signing APK."""
    package = os.environ.get("TWA_PACKAGE_NAME", "cc.grepon.school")
    fingerprint = os.environ.get("TWA_CERT_FINGERPRINT", "PLACEHOLDER_REPLACE_WITH_APK_SIGNING_CERT_SHA256")
    return jsonify([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": package,
            "sha256_cert_fingerprints": [fingerprint],
        },
    }])
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_sync.py -v
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_sync.py
git commit -m "feat: /.well-known/assetlinks.json route for TWA domain verification"
```

---

### Task 6: Update `school-sync.sh` to write sync_meta

**Files:**
- Modify: `sync/school-sync.sh`

- [ ] **Step 1: Update IXL, SGY, and GC scrape steps**

In `sync/school-sync.sh`, replace the three scraper blocks (Steps 1, 2, 2b) with these updated versions that write sync_meta after success:

```bash
# Helper: write sync metadata for a source
write_sync_meta() {
    local source="$1" result="$2"
    python3 -c "
from school_dashboard.sync_meta import write_sync_source
write_sync_source('${source}', '${result}')
" 2>/dev/null || true
}

# --- Step 1: IXL scrape ---
if [[ -x "$IXL_CRON" ]]; then
    log "Running IXL scrape..."
    if bash "$IXL_CRON"; then
        write_sync_meta "ixl" "ok"
    else
        log "WARN: IXL scrape had errors"
        write_sync_meta "ixl" "error"
    fi
else
    log "WARN: IXL cron script not found at $IXL_CRON — skipping"
fi

# --- Step 2: Schoology scrape ---
if command -v sgy &>/dev/null; then
    log "Running Schoology scrape..."
    if sgy summary --json > "$SGY_FILE" 2>/dev/null; then
        write_sync_meta "sgy" "ok"
    else
        log "WARN: SGY scrape had errors"
        write_sync_meta "sgy" "error"
    fi
else
    log "WARN: sgy command not found — skipping"
fi

# --- Step 2b: GameChanger scrape (non-fatal if not configured) ---
if command -v gc &>/dev/null && [[ -n "${GC_TOKEN:-}${GC_EMAIL:-}" ]]; then
    log "Running GameChanger scrape..."
    if bash /app/sync/gc-scrape.sh; then
        write_sync_meta "gc" "ok"
    else
        log "WARN: GC scrape had errors"
        write_sync_meta "gc" "error"
    fi
else
    log "INFO: gc not configured — skipping (set GC_TOKEN or GC_EMAIL in config/env)"
fi
```

The complete updated `sync/school-sync.sh` becomes (replace lines 1–48):

```bash
#!/usr/bin/env bash
# school-sync.sh — Data refresh: scrape all sources, update state, regenerate dashboard.
# Runs via system cron at 6:00am and 2:30pm. No LLM needed.
#
# Usage:
#   ./school-sync.sh                      # uses defaults
#   IXL_CRON=/opt/ixl/cron/ixl-cron.sh ./school-sync.sh
#
# Crontab:
#   0 6 * * *    /opt/school-dashboard/school-sync.sh 2>>/tmp/school-sync.log
#   30 14 * * 1-5 /opt/school-dashboard/school-sync.sh 2>>/tmp/school-sync.log

set -euo pipefail

ENVFILE="${SCHOOL_DASHBOARD_ENV:-/etc/school-dashboard/env}"
[[ -f "$ENVFILE" ]] && { set -a; source "$ENVFILE"; set +a; }

IXL_CRON="${IXL_CRON:-/opt/ixl/cron/ixl-cron.sh}"
IXL_DIR="${IXL_DIR:-/tmp/ixl}"
SGY_FILE="${SGY_FILE:-/tmp/schoology-daily.json}"

log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }

# Helper: write sync metadata for a source (non-fatal)
write_sync_meta() {
    local source="$1" result="$2"
    python3 -c "
from school_dashboard.sync_meta import write_sync_source
write_sync_source('${source}', '${result}')
" 2>/dev/null || true
}

# --- Step 1: IXL scrape ---
if [[ -x "$IXL_CRON" ]]; then
    log "Running IXL scrape..."
    if bash "$IXL_CRON"; then
        write_sync_meta "ixl" "ok"
    else
        log "WARN: IXL scrape had errors"
        write_sync_meta "ixl" "error"
    fi
else
    log "WARN: IXL cron script not found at $IXL_CRON — skipping"
fi

# --- Step 2: Schoology scrape ---
if command -v sgy &>/dev/null; then
    log "Running Schoology scrape..."
    if sgy summary --json > "$SGY_FILE" 2>/dev/null; then
        write_sync_meta "sgy" "ok"
    else
        log "WARN: SGY scrape had errors"
        write_sync_meta "sgy" "error"
    fi
else
    log "WARN: sgy command not found — skipping"
fi

# --- Step 2b: GameChanger scrape (non-fatal if not configured) ---
if command -v gc &>/dev/null && [[ -n "${GC_TOKEN:-}${GC_EMAIL:-}" ]]; then
    log "Running GameChanger scrape..."
    if bash /app/sync/gc-scrape.sh; then
        write_sync_meta "gc" "ok"
    else
        log "WARN: GC scrape had errors"
        write_sync_meta "gc" "error"
    fi
else
    log "INFO: gc not configured — skipping (set GC_TOKEN or GC_EMAIL in config/env)"
fi
```

Keep lines 49–94 (Steps 3–6) unchanged.

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -q
```

Expected: all tests pass (no test for the shell script — it's verified in production)

- [ ] **Step 3: Commit**

```bash
git add sync/school-sync.sh
git commit -m "feat: school-sync.sh writes sync_meta after each scraper step"
```

---

### Task 7: Env vars, config updates, CLAUDE.md

**Files:**
- Modify: `config/env.example`
- Modify: `.env.example`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `SCHOOL_SYNC_META_PATH` and TWA vars to `config/env.example`**

In the `# Paths (defaults work inside Docker…)` section, add two lines:

```bash
# SCHOOL_SYNC_META_PATH=/app/state/sync_meta.json

# TWA (Android APK via Bubblewrap — fill in after building the APK)
# TWA_PACKAGE_NAME=cc.grepon.school
# TWA_CERT_FINGERPRINT=AA:BB:CC:...
```

- [ ] **Step 2: Apply same additions to `.env.example`**

Find the `# Paths (defaults work inside Docker…)` section and add the same lines.

- [ ] **Step 3: Update `CLAUDE.md`**

In the **State Files** table, add a row:

```markdown
| `sync_meta.json` | Per-source scrape timestamps: `{ixl: {last_run, last_result}, sgy: ..., gc: ...}` |
```

In the **Config** section, add after the GameChanger paragraph:

```
`SCHOOL_SYNC_META_PATH` overrides the sync metadata path (default: `/app/state/sync_meta.json`). TWA domain verification: `TWA_PACKAGE_NAME` (default: `cc.grepon.school`), `TWA_CERT_FINGERPRINT` (populate after signing APK).
```

In the **API Endpoints** table, add:

```markdown
| `/api/sync/meta` | GET | Per-source sync freshness (`{ixl: {last_run, last_result}, ...}`) |
| `/.well-known/assetlinks.json` | GET | TWA domain verification (fingerprint from `TWA_CERT_FINGERPRINT` env var) |
```

In the **Tests** section, update the count and test_sync.py line:

```markdown
91 tests across 6 files. All use mocks — no live credentials needed.

tests/test_db.py              7 tests  — SQLite schema, dedup, facts
tests/test_calendar_import.py 12 tests — PDF parsing, event classification
tests/test_intel.py           4 tests  — LiteLLM extraction, error handling
tests/test_digest.py          46 tests — digest build, ntfy send, gc event loading + card rendering, build_quick_check
tests/test_sync.py            15 tests — /api/sync auth, concurrency, /api/sync/status, /api/sync/meta, assetlinks, freshness, sync_meta writes
tests/test_sync_meta.py       7 tests  — sync_meta module read/write
```

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest tests/ -q
```

Expected: all tests pass

- [ ] **Step 5: Commit and push**

```bash
git add config/env.example .env.example CLAUDE.md
git commit -m "docs: add sync_meta + TWA vars to env examples and CLAUDE.md"
git push
```
