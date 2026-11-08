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
4. Tests named `test_silent_degradation_*` / `test_inconsistency_*` pin unguarded paths (gap ids
   below, as in the PR body); they pass today by design. When one fails with "UNGUARDED gap Gn
   closed (or changed)", the gap was fixed or the behaviour moved: flip that test to assert the
   guarded behaviour (a warning or a clear failure).
5. Update the pin SHAs in the table above.

## Unguarded gap ids

| Id | Gap |
|----|-----|
| G1 (closed) | ixl: `remaining` renamed inside `totals` reads as "all done" |
| G2 (closed) | ixl: payload with only the `remaining` list is accepted with empty totals |
| G3 (closed) | ixl: `summary_text` KeyError when totals lack `done`/`assigned` |
| G4 (closed) | sgy: `per_child` renamed merges nothing, no warning |
| G5 (closed) | sgy: missing `child.name` raised a bare IndexError; now that entry is skipped and `scrape_meta` records `?` ("a child in the last scrape had no name") |
| G6 (closed) | sgy: `due_date` renamed dropped every dated row; a child with an assignment lacking `due_date` now keeps previous data and is marked unavailable (`unexpected`) |
| G7 (closed) | sgy: `title` renamed collapsed action items to one "Unknown"; same guard as G6, and the child is also untrusted for auto-archive |
| G8 (closed) | sgy: failed child reads as "SGY all done" |
| G9 (closed) | sgy: auto-archive trusts partial/failed children (no top-level `warnings`). Partial children are deliberately never trusted for archiving and not warned about |
| G10 (closed) | sgy: readiness now lowercases status and shares `overview_items.DONE_STATUSES` (so `Submitted`, `Turned in` count as done everywhere) |
| G11 (closed) | sgy: readiness uses `overview_items.parse_date`, so `Month D, YYYY` due dates count there too |
| G12a/b/c (closed) | powerschool: missing `name` / renamed item `due` / missing item `title` now add a `powerschool_meta` warning and keep the child's previous data (a nameless entry is skipped). These warnings reach `/api/dashboard`, not the overview banner |
| G13 (closed) | gc: a team whose summary has no `schedule` list keeps its previous schedule (or none), gets `schedule_error: missing_schedule` and a banner/digest warning with the last-good date. A genuine `schedule: []` is not flagged. If every team fails the scrape still refuses to overwrite |
| G14a/b/c (closed) | gc: when a team's events all lack a readable `date`, or the `time` / `type` key is absent from every event, `scrape_meta.gc_warnings` reports it (overview `warnings`, digest DATA WARNINGS block). Present-but-empty values are not flagged |
| G16 (closed) | ixl: roster child that had IXL data but has no file now (sync deletes a failed child's file) read as "IXL fine"; now `scrape_meta` marks it unavailable (`missing`). A roster child that never had IXL data is not flagged |
| G15 (closed) | readiness also walks the configured roster, so a child with IXL data but no Schoology entry gets an IXL checklist |
