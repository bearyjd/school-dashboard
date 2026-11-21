# Reconcile (recon v2) go-live, day one and rollback

Reconcile is the new default screen at `/app/`: one list per kid of what to do tonight, what waits on a teacher and what needs action, merged from Schoology, PowerSchool and IXL. There is **no shadow week**: the screen appears by itself as soon as v2 is enabled and migrated. Everything is behind flags; the v1 data, digests and the old Today screen are untouched and stay available.

Run commands on the Docker host from the compose directory. Replace `<name>` with the container name and use the repo's placeholders for hosts.

## Go-live, in this order

1. **Deploy the image** with this release (`docker compose pull && docker compose up -d`); with no flags set nothing changes (`/app/` still shows Today).
2. **Backup**: `school-state transition maintenance on --reason "recon go-live"`, then `school-state transition backup`. Maintenance stays on until step 6.
3. **Write token and host settings** in `config/env` (never paste them into chat or tickets):
   - `RECON_WRITE_TOKEN=<64 hex chars>` from `python3 -c "import secrets; print(secrets.token_hex(32))"` (at least 32 characters; shorter means writes answer 501).
   - Host allowlist and port binding as in `docs/ops/host-allowlist-and-port-binding.md` (`ALLOWED_HOSTS`, `HOST_CHECK_MODE`, `DASHBOARD_BIND_ADDR`). `RECON_WRITE_OPEN=1` skips the token and is only for a trusted private network, never a public hostname.
4. **Dry run**: `docker exec <name> school-state assignments migrate --dry-run` (it runs the real write path on a private temporary copy of the database, so it predicts the write and changes nothing real; read the counts, the warnings and any refusal reason).
5. **Migrate**: `docker exec <name> school-state assignments migrate`. It copies legacy items and action items into assignments and writes a backup first. Exit 3 means maintenance is off or a sync is running.
   - Exit codes: 0 done; 1 failed, nothing changed; 2 unusable input (state file or config), nothing changed; 3 refused (maintenance off or a sync running); 4 committed, but some rows were skipped and remain only in the legacy store. On 4, read `skipped_keys` in the report, fix those rows, and re-run; re-running is safe. If every row was skipped, the migration is not marked done (ingest stays gated) until a re-run succeeds.
   - A kid named `"(unknown)"` appears if a legacy row had no child name.
   - Pending legacy items with a past due date show under Needs action (Missing) on day one until they are dealt with or marked done.
6. `school-state transition maintenance off`.
7. **Turn the ingest on**: `RECON_INGEST_ENABLED=1` in `config/env`, `docker compose up -d`.
   **Mandatory smoke step, before anything else** (the flag is only live after the container restarts):
   - `curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/api/v2/summary` must print `200`, and so must the same command on `http://127.0.0.1:5000/api/v2/assignments?limit=1`.
   - `docker exec <name> school-state assignments digest --mode night` must exit 0.
   - If any of these fail, do not continue: roll back now (set `RECON_INGEST_ENABLED=0` or remove the line in `config/env`, `docker compose up -d`, confirm `GET /api/v2/config` shows `enabled: false` and `/app/` shows Today). The traceback is in the container log (`docker logs <name> 2>&1 | grep -B2 -A15 'api v2: unexpected'`; the CLI prints it on stderr); the request log line carries a traceback (our own frames only: no local variables, and the exception message is withheld), never row text.
8. **Run a sync**: `docker exec <name> /app/sync/school-sync.sh` (or wait for the next cron). The merge runs at the end of `school-state update`.
9. **Check**: `docker exec <name> school-state assignments report` and `docker exec <name> school-state assignments digest --mode morning` (preview only, sends nothing).
10. **Open `/app/`**: Reconcile shows by itself (`GET /api/v2/config` answers `enabled` and `migrated`). Close and reopen an installed PWA once so it takes the new service worker (cache `school-v4`).
11. **Each device, once**: Settings, section "Assignments": "Who is using this device" (name and Parent or Kid) and "Write token". Both are kept only in that browser (`school-actor`, `school-recon-token`). Without them the action buttons open "Set up this device".
12. **Later**: `RECON_DIGEST_ENABLED=1` (replaces the LLM morning and night digests, see `docs/ops/recon-digest.md`), then, once the digest looks right, `RECON_PUSH_ENABLED=1` (immediate pushes).

## Who can change things

Parent/Kid is a label for the history, not a permission. Anyone holding the write token can act as a parent, so enter the token only on devices you would trust with that.

To rotate the token (a lost device, or someone who should no longer have it): change `RECON_WRITE_TOKEN` in `config/env`, run `docker compose up -d`, then re-enter the new token in Settings on each device you still trust. The old token stops working at once.

## Day one: what to check

- `school-state assignments report` (add `--redact` before pasting it anywhere): merge rate per kid, unmatched pairs, alias suggestions, possible duplicates. A pair that is the same class under two names gets `school-state assignments alias add --kid NAME --from "Alias" --to "Canonical"`; the next sync re-merges.
- A "Possible duplicate" chip means the merge found two candidates and did not guess; note them and compare with the report's unmatched pairs.
- `/api/v2/summary` (or the banner at the top of Reconcile): `ingest_ok` true and no "Not verified" banner after a fresh sync. A source that is overdue shows "Not verified: <source> data is out of date"; a kid with nothing open then reads "Nothing open (not verified)", not "All clear".
- Tap Done on a row, reload, Undo: it must persist and restore.
- Draft note on a Disputed or Missing row: it is only a draft (copy, open in your mail app, open in portal). The server never sends mail: a teacher note is a draft that the parent sends from their own mail app. Teacher name and email come from the class settings.
- `school-state watchdog --dry-run`: no `recon_ingest` problem is reported.

## Rollback

No image rollback is needed.

1. Set `RECON_INGEST_ENABLED=0` (and `RECON_DIGEST_ENABLED=0`, `RECON_PUSH_ENABLED=0` if set) in `config/env`; `docker compose up -d`.
2. `/app/` shows Today again on the next load (with the flag off `/api/v2/config` answers 200 with `enabled: false`; the other `/api/v2/*` routes answer 404; no error is shown). `/today` and `/classic/*` are always available, and `/recon` then says it is not switched on.
3. v1 data and digests were never changed. The v2 tables and their data stay in `school.db`; keep them for a later retry or dump them with `docker exec <name> school-state assignments export > recon-export.json` (contains titles; keep it private).
4. To go live again, set the flag back to `1` and run a sync; no new migration is needed.

## Data problems (warnings you may see)

One malformed stored row never fails a request; it is skipped or treated as "no value" and counted in a warning (counts only, never titles) in `/api/v2/summary` `warnings`, the digest `Notes:` line, `assignments report` (text and `--json`) and the shadow-ingest report. One WARNING is also logged per process run.

- **"N calendar rows had an unreadable date; ignored"**: some `events` row has a `date` that is not a real date (for example `2025-09-31`). Such a row cannot mark a no-school day, so check the row and fix or delete it. The container has no `sqlite3` binary, so find it with Python (read-only, ids and dates only, no titles). SQLite `date()` rolls `2025-09-31` over to `2025-10-01`, so the query compares it with the text:
  `docker exec <name> python3 -c "import sqlite3; c = sqlite3.connect('file:/app/state/school.db?mode=ro', uri=True); print(c.execute('SELECT id, date FROM events WHERE date(date) IS NULL OR date(date) <> substr(date, 1, 10)').fetchall())"`
- **"N assignment rows had unreadable dates"**: a stored `due_date`, `stage_since` or `closed_at` could not be read. The row is still shown (with a "Data problem" chip, `info: data_problem`), the bad due date counts as no due date and an unreadable `stage_since` is replaced by the row's `created_at`. Find them the same way (ids and raw dates only; impossible days such as `2025-09-31` are caught by comparing `date(x)` with `substr(x, 1, 10)`, as for events):
  `docker exec <name> python3 -c "import sqlite3; c = sqlite3.connect('file:/app/state/school.db?mode=ro', uri=True); print(c.execute('SELECT id, due_date, stage_since, closed_at FROM assignments WHERE (due_date IS NOT NULL AND (date(due_date) IS NULL OR date(due_date) <> substr(due_date, 1, 10))) OR stage_since IS NULL OR date(stage_since) IS NULL OR date(stage_since) <> substr(stage_since, 1, 10) OR (closed_at IS NOT NULL AND (date(closed_at) IS NULL OR date(closed_at) <> substr(closed_at, 1, 10)))').fetchall())"`
- **"N child(ren) could not be loaded"**: building one kid's section raised unexpectedly; that kid shows `error: unavailable` and the others are unaffected. The log line names the exception type; report it.

See also `docs/ops/spa-redesign-rollout.md` (SPA deploy, sync token), `docs/ops/recon-digest.md`, `docs/ops/host-allowlist-and-port-binding.md`.
