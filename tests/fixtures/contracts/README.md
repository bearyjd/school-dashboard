# Scraper contract fixtures

Fully synthetic, de-identified samples of what each vendored scraper emits with `--json`.
They pin the output shape the dashboard consumes (`tests/test_contract_*.py`). No real names,
emails, URLs, schools, teams or IDs: children are Alpha/Beta/Gamma Tester, courses are
"Sample ...", hosts use `.invalid`.

| File | Scraper command | Derived from (submodule pin on main) |
|------|-----------------|--------------------------------------|
| `ixl.json` | `ixl assigned --json` | `vendor/ixl-scrape` 24e25ab: `cmd_assigned` + `render_json_result` (flat envelope: status/warnings/errors + totals/remaining) and the skill dict in `scrapers/skills.py` |
| `sgy.json` | `sgy summary --json` | `vendor/schoology-scrape` 1094892: `cmd_summary`, `build_failed_child`, `StageTracker` (three children: high / partial / failed confidence) |
| `gc.json` | `gc teams --json` + `gc summary --json --team ID` | `vendor/gc` bb52350 (v0.1.36): `_normalize_team`, `_normalize_event`, `get_team_summary`. Stored as `{"teams": [...], "summaries": {team_id: summary}}` so one file replays both commands |
| `powerschool.json` | `powerschool summary --json` | `vendor/powerschool-scrape` is not vendored in CI; shape follows the contract documented in `school_dashboard/powerschool.py` and the existing `tests/fixtures/powerschool-summary.json`. Re-derive when that scraper is available |

The shapes were read from the scrapers' source in local submodule checkouts (not captured from live runs; the ixl and sgy checkouts were not verified to sit exactly at the pinned SHA). Where a scraper
can emit variants the fixture includes them on purpose: a capitalised status (`Submitted`),
a free-form due date (`November 12, 2026`), an empty due date, a failed child.

## Dates

All dates are relative to the anchor day 2026-11-05. `tests/contract_support.py::load` shifts
every ISO or `Month D, YYYY` string so the anchor becomes the real today; freshness and
due-date logic therefore behaves as in production.

## Refreshing after a submodule bump

1. Read the scraper's JSON-emitting code (see the table) and `git diff` the old and new pin.
2. Update the fixture by hand with new or renamed keys, keeping the data synthetic.
3. Run `pytest tests/test_contract_*.py`. A failure in `test_fixture_has_the_keys_consumers_read`
   or `test_fixture_is_accepted_end_to_end` means a consumer needs updating, not the test.
4. Tests named `test_silent_degradation_*` document unguarded paths; they pass today by design.
   If you add a guard, flip the matching test to assert the new warning.
5. Update the pin SHAs in the table above.
