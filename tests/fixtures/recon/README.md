# Recon v2 fixtures (SYNTHETIC)

Every file here is synthetic: invented kids (Alex, Sam, Jo, Zed), invented classes and titles, and
hand-picked timestamps around Wed 2026-11-18 (EST, UTC-05:00). Nothing was captured from a live
scraper (plan 10.1 allows redacted real captures, but none exist yet for these scenarios).

- `flags/`      one file per flag (`do_tonight`, `waiting_on_teacher`, `done_not_received`, `disputed`,
                `missing`, `unverified`, `closed`, `upcoming`, `info_chips`). Each scenario: `now`, `no_school`,
                `grace_days`, `stale_sources`, `ingest_ok`, `state` (overrides over defaults in
                `tests/recon/support.py`), `links`, `expect`.
- `merge/`      `match()` scenarios: record, candidates, link_index, aliases, expected MatchResult.
- `sequences/`  ordered steps (`scrape` / `tap` / `tick`); `expect` after each step is a subset match on the
                record state (stage, evidence, portal_status, flag, per-link live/trusted_misses, transition).
                Run by `tests/recon/support.py::run_sequence`, which composes the pure modules the way the
                S3 ingest will (register_scrape -> combine_portal_status -> portal_decision -> compute_flag).
- `migration/`  legacy `items` + state `action_items` (golden: 13 rows in -> 12 created + 1 merged;
                completed_history: everything completed, Needs action must stay empty).
