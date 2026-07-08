# Agent-Native Roadmap — school-dashboard

**Update 2026-07-07 (executor agent):** Item 3's compounding behavioral bug (missing
`events` table) is now **FIXED** — `db.py::init_db` creates the `events` table via a
new `init_events_table()` following the existing `items`/`digests` inline-migration
pattern (columns: `id, date, title, type, child, source, created_at`, matching what
`readiness.py::get_checklist` selects). Regression test added:
`tests/test_readiness.py::test_init_db_creates_events_table_and_checklist_surfaces_test_event`
— uses the real `db.init_db()` path (not a hand-crafted table like the existing
`tmp_db` fixture), confirmed to fail with `sqlite3.OperationalError: no such table:
events` before the fix and pass after. Full suite: 125 passed (124 → 125). The bare
`except Exception` in `readiness.py::get_checklist` was left in place as defensive
handling for genuinely unexpected errors, but it no longer masks a guaranteed-missing
table for any db created via the normal init path.

Item 2 (CI test gate) is now **FIXED** — added `.github/workflows/tests.yml`
(checkout, setup-python 3.12, `pip install -e ".[server]"`, `pytest tests/`).
Verified none of the 125 tests import vendor packages at module scope, so no vendor
install step was needed.

Item 3's doc-staleness half is **partially addressed** — `README.md` and
`docs/RUNBOOK.md` corrected to drop live `calendar_import.py`/`intel.py` references
(they already didn't appear in `docs/CONTRIBUTING.md`). `CHANGELOG.md`'s `[1.0.0]`
historical entry was left untouched (it correctly describes what shipped at that
version) — no `[Unreleased] > Removed` entry was added since that's a product/release
decision outside this pass's scope.

Item 1 (pytest collection at repo root / `testpaths` in `pyproject.toml`) was
**not touched** — out of scope for this pass per task instructions; `pytest tests/`
remains the documented, working command.

Items 4 and 5 were **not touched** — out of scope for this pass.


Prioritized by **Human-Attention-Saved per Unit of Effort**. All findings below were
verified by actually running commands in this repo on 2026-07-07 (not inferred from
reading docs alone). No student data, credentials, or secret values appear in this
file or in `agent_native/` — file paths and command output shapes only.

---

## Top 5 — immediately actionable

### 1. Fix `pytest` collection at repo root (trivial fix, blocks every agent's first move)

**Problem (verified):** `CLAUDE.md` and `README.md` both document `pytest` (bare, from
repo root) as the standard test command. Running it as documented fails immediately:

```
ERROR vendor/ixl-scrape/tests - _pytest.pathlib.ImportPathMismatchError: ('tests.conftest',
  '.../vendor/gc/tests/conftest.py', PosixPath('.../vendor/ixl-scrape/tests/conftest.py'))
ERROR vendor/schoology-scrape/tests/test_summary.py - ModuleNotFoundError: No module named 'tests.test_summary'
```

This happens because `vendor/{ixl-scrape,schoology-scrape,gc}` are separate git repos
each with their own `tests/conftest.py` / `tests/test_*.py`, and pytest's rootdir-based
collection picks all of them up and collides on the identical module name `tests`.
`pytest tests/` (scoped) passes cleanly: **124 tests, 0 failures** once `flask` +
`requests`/`pypdf`/`beautifulsoup4`/`jinja2`/`pdfminer.six` are installed.

An agent picking up any bug report will run the documented `pytest` command first,
see 5 collection errors, and burn a turn (or several) debugging pytest internals
before discovering the vendor/ directories are the cause — pure wasted effort with
zero relation to the actual bug.

**Fix:** add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```
**Acceptance criteria:** `pytest` (no args, from repo root) collects only `tests/*.py`
and exits 0 with the same pass count as `pytest tests/` today. No changes needed
under `vendor/` (do not touch those repos).

---

### 2. Add a CI workflow that actually runs the test suite

**Problem (verified):** `.github/workflows/` contains exactly one workflow,
`docker.yml`, which builds and pushes the Docker image on every push to `main` —
it never runs `pytest`. There is no automated gate that catches a regression before
it ships. Combined with item 1, a contributor (human or agent) could push code with
a real test failure and CI would still go green.

**Fix:** add `.github/workflows/tests.yml` running (after item 1's fix):
```yaml
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
  with: { python-version: "3.12" }
- run: pip install -e ".[server]"
- run: pytest
```
(No vendor `pip install -e` needed for `tests/` — none of the 124 tests import the
vendor packages directly, only `web/app.py` and `school_dashboard/*`, which don't
import `ixl`/`sgy`/`gc` at module scope.)

**Acceptance criteria:** new workflow file present; a PR with a deliberately broken
test fails the check; a clean PR passes.

---

### 3. Reconcile stale architecture docs — `intel.py` and `calendar_import.py` no longer exist

**Problem (verified):** `school_dashboard/` currently contains only: `cli.py, db.py,
digest.py, email.py, gcal.py, html.py, llm.py, readiness.py, state.py, sync_meta.py,
__init__.py`. There is **no** `intel.py` and **no** `calendar_import.py` on disk.
Yet all of the following still describe them as live, current architecture:

- `CLAUDE.md` — Architecture section, Key Data Flows, one-off command example
- `README.md` — Architecture diagram, cron description
- `docs/CONTRIBUTING.md` — file table
- `docs/RUNBOOK.md` — daily schedule description

`sync/school-sync.sh` itself already knows this — it guards the intel step with
`if python3 -c "import school_dashboard.intel" >/dev/null 2>&1` and skips cleanly
if absent (see `CHANGELOG.md` `[1.0.0]` "Added" listing both modules, with no
matching "Removed" entry in `[Unreleased]` — the removal happened silently outside
changelog discipline).

An agent asked to "fix a bug in email-derived calendar events" or "the intel
extraction step" will go looking for a file that doesn't exist, or worse, assume
the sync script's guarded skip is itself the bug.

**Compounding behavioral bug (verified, not just a docs problem):** nothing in the
current codebase creates the SQLite `events` table anymore — `grep -rn "CREATE
TABLE" school_dashboard/*.py web/app.py` only finds `items` and `digests`.
`calendar_import.py` (removed) used to be the thing that created and populated
`events`. Yet `school_dashboard/readiness.py::get_checklist` still runs
`SELECT date, title, type, child FROM events WHERE type IN ('TEST','QUIZ') ...`
against it, wrapped in a bare `try/except Exception` that logs a warning and
silently returns `test_events = []` on failure (`sqlite3.OperationalError: no such
table: events` included). **In a real deployment today, the "tests/quizzes" line
item in the readiness checklist can never appear — it is silently dead
functionality**, not a documented limitation. This is a genuine product bug hiding
behind stale docs, worth fixing (recreate `events` table + a replacement for
calendar import, or remove the dead query path and update `format_checklist_text`/
docs to stop promising test/quiz readiness items) rather than only relabeling docs.

**Fix applied here:** `CLAUDE.md` in this repo has been updated (see below) to drop
the `intel.py`/`calendar_import.py` references, flag the dead `events`-table query
in `readiness.py`, and describe the sync pipeline as it actually runs today (IXL →
SGY → GC → state → email-sync → HTML → digest, intel step permanently absent).

**Remaining work (not done in this pass — outside the two files this audit is
scoped to touch):** apply the same correction to `README.md`, `docs/CONTRIBUTING.md`,
and `docs/RUNBOOK.md`, and add a `[Unreleased] > Removed` entry in `CHANGELOG.md`
documenting when/why `intel.py` and `calendar_import.py` were dropped (if calendar
import is meant to return, say so explicitly instead of leaving stale docs).

**Acceptance criteria:** `grep -rn "intel.py\|calendar_import" README.md docs/
CHANGELOG.md` returns either nothing, or an explicit "removed in vX, replaced by Y /
not currently implemented" note — never a description implying the file is live.

---

### 4. Add recorded-fixture contract tests between the three scrapers and school-dashboard

**Problem (verified):** Every test file that consumes scraper-shaped data
(`test_readiness.py`, `test_digest.py`, `test_items.py`, `test_api_items.py`) builds
its input by hand-writing Python dicts inline (e.g. `test_readiness.py`'s `tmp_state`
fixture literally types out `{"schoology": {"Jack": {"assignments": [...]}}}`).
There is **no** `tests/fixtures/` directory, no recorded JSON captured from a real
`sgy summary --json` / `ixl assigned --json` / `gc summary --json` run anywhere in
this repo, and no `jsonschema`/`pydantic` contract check (`grep -rn "jsonschema|
schema|validate"` across `school_dashboard/*.py` and `web/app.py` returns nothing).

The three scrapers are independent repos (`vendor/ixl-scrape`, `vendor/
schoology-scrape`, `vendor/gc`) updatable via `git submodule update --remote` with
no dashboard-side check that the new version's JSON shape still matches what
`state.py`/`readiness.py`/`digest.py` expect. A scraper author renaming
`due_date` → `dueDate`, or changing `status` enum values, would pass every existing
dashboard test (because the tests never touch real scraper output) while silently
breaking `readiness.py`'s `date.fromisoformat(due_str)` parsing or urgency
classification in production.

**Fix:**
1. Capture one de-identified sample output per scraper (hand-edit any real names/
   grades out — replace with placeholder strings like `"Child A"`, fabricated dates)
   and commit as `tests/fixtures/{ixl,sgy,gc}_sample.json`.
2. Add `tests/test_contract.py` that loads each fixture and asserts the shape
   `state.py`, `readiness.py`, and `digest.py` actually destructure (top-level keys,
   nesting, and field names/types they `.get()` off of) — this turns "the vendored
   scraper's output shape" into an explicit, versioned assertion instead of tribal
   knowledge spread across three separate git histories.
3. Optionally wire `tests/test_contract.py` to also run (as a separate, allowed-to-
   skip job) against the *actual* `vendor/*` CLI's `--json` output in CI using a
   mocked HTTP layer, so a `git submodule update --remote` bump gets flagged before
   merge if the shape changed.

**Acceptance criteria:** `tests/fixtures/` exists with 3 sample files (no real
student names/grades); `tests/test_contract.py` fails if a required key that
`readiness.py`/`state.py` currently reads is removed from a fixture.

---

### 5. Fix inconsistent `DB_PATH` resolution in `web/app.py` (import-time constant vs. per-request env read)

**Problem (verified):** `web/app.py` defines a module-level constant at import time:
```python
DB_PATH = os.environ.get("SCHOOL_DB_PATH", "/opt/school/state/school.db")   # line 23
```
and uses that frozen value in `load_upcoming_events` (lines 39/44) and the
`/api/readiness`, `/api/digest/<id>`, `/api/digest/<id>/cards/<index>` handlers
(lines 462/472/473/489/490). But six other route handlers instead re-read the env
var fresh on every request: `db_path = os.environ.get("SCHOOL_DB_PATH", ...)`
(lines 187, 280, 294, 322, 341, 521).

`tests/test_sync.py` relies on `monkeypatch.setenv("SCHOOL_DB_PATH", ...)` working —
and it does, for the routes that re-read per request. But an agent writing a new
test for `/api/readiness` or `/api/digest/*` using the same, already-established
`monkeypatch.setenv` pattern from `test_sync.py` will get a test that silently uses
the wrong (stale, real default-path) database, producing a confusing failure that
looks like a readiness-logic bug but is actually this inconsistency. This already
happened by omission: `tests/test_readiness.py` never exercises the Flask route at
all (it calls `readiness.get_checklist` directly) — precisely because going through
`/api/readiness` would hit this trap.

**Fix:** make all `DB_PATH` reads consistent — either (a) remove the module-level
constant and change all six/seven usages to read `os.environ.get("SCHOOL_DB_PATH",
...)` fresh, or (b) keep a single frozen constant and provide a documented
`app.config["DB_PATH"]` override point for tests, then update all handlers to use
it uniformly.

**Acceptance criteria:** a new test `test_readiness_route_uses_test_db_path` in
`tests/test_api_items.py` or a new `tests/test_readiness_route.py`, following the
exact `monkeypatch.setenv("SCHOOL_DB_PATH", ...)` pattern already used in
`test_sync.py`, passes without needing to know about the import-time-vs-per-request
distinction.

---

## Also observed (lower priority / higher effort — not in top 5)

- **`vendor/*` submodule bumps are judgment calls with no safety net.** `git
  submodule update --remote` (documented in `CLAUDE.md`) blindly takes each
  vendor's default-branch HEAD; combined with item 4's missing contract tests,
  this is the actual mechanism by which schema drift would reach production
  undetected. Fixing item 4 substantially de-risks this.
- **Full `sync/*.sh` chain cannot be reproduced offline.** `tests/
  test_wrapper_scripts.py` explicitly says so in its own docstring: "We test the
  LOGDIR derivation logic by running bash snippets directly rather than running
  the full scripts (which need live credentials and all services)." Only the
  `LOGDIR` bash-snippet logic is exercised; `sync/school-sync.sh`'s actual
  orchestration (steps 1–6) and `sync/gc-scrape.sh`'s curl/JSON-merge logic have
  zero test coverage and can only be verified by a live run against real
  IXL/Schoology/GameChanger accounts. This is a large effort to fix properly
  (would need a fake local HTTP server standing in for all three scraper CLIs)
  and is deprioritized below items 1–5.
- **`web/spa` (Vite/React) has no automated tests** — `package.json` devDependencies
  contain no `vitest`/`jest`/testing-library. `npm run build` (`tsc -b && vite
  build`) is the only verifiable step, and nothing wires it into CI (see item 2 —
  a CI workflow addition should include this build as a smoke check even before
  real frontend tests exist).
- **`readiness.py` and the "quick check" path in `digest.py` are fully deterministic
  and well-tested** (8 and part of 46 tests respectively, pure functions, no LLM
  calls) — these are *not* a judgment-call chokepoint; an agent can safely modify
  and re-verify them with `pytest tests/test_readiness.py` alone. The actual
  judgment chokepoint is narrower than it first appears: only the **LLM-driven full
  digest text** (`build_digest_text` in `digest.py`, and `intel.py`'s now-removed
  email-extraction) required a human's prior judgment about prompt wording/tone —
  and that boundary is already isolated behind `school_dashboard/llm.py`'s
  `chat_completion()`, which every existing test mocks at the `requests.post`
  layer. An agent changing digest *formatting* doesn't need any LLM judgment call;
  only changing the *prompt itself* does.

## Secrets/credentials finding (informational, already handled correctly — verify, don't regress)

`secrets/` contains what appear to be real credential files: `secrets/
client_secret.json`, `secrets/env`, `secrets/ixl-accounts.env`, `secrets/gog/
credentials.json` (paths only — contents were not read beyond confirming existence).
Verified:
- `secrets/` **is** covered by `.gitignore` (`git check-ignore -v` confirms all four
  paths match the `secrets/` rule at `.gitignore:57`).
- `git ls-files secrets/` returns nothing, and `git log --all --oneline -- secrets/`
  returns nothing — these files have **never** been committed to this repo's git
  history.
- `STRUCTURE.md` already documents this directory with an explicit "DO NOT COMMIT"
  comment.

No action required beyond continued vigilance — but see the `CLAUDE.md` update for
an explicit rule captured going forward (do not `git add -f` anything under
`secrets/`, and treat any future `git status` output showing `secrets/` as untracked-
but-tripped as a stop-and-check signal, not a thing to force-add).
