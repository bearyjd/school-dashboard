# Reconcile (recon v2) go-live, day one and rollback

Reconcile is the new default screen at `/app/`: one list per kid of what to do tonight, what waits on a teacher and what needs action, merged from Schoology, PowerSchool and IXL. There is **no shadow week**: the screen appears by itself as soon as v2 is enabled and migrated. Everything is behind flags; the v1 data, digests and the old Today screen are untouched and stay available.

Run commands on the Docker host from the compose directory. Replace `<name>` with the container name and use the repo's placeholders for hosts.

## Go-live, in this order

1. **Deploy the image** with this release (`docker compose pull && docker compose up -d`); with no flags set nothing changes (`/app/` still shows Today).
2. **Backup**: `school-state transition maintenance on --reason "recon go-live"`, then `school-state transition backup`. Maintenance stays on until step 6.
3. **Write token and host settings** in `config/env` (never paste them into chat or tickets):
   - `RECON_WRITE_TOKEN=<64 hex chars>` from `python3 -c "import secrets; print(secrets.token_hex(32))"` (at least 32 characters; shorter means writes answer 501).
   - Host allowlist and port binding as in `docs/ops/host-allowlist-and-port-binding.md` (`ALLOWED_HOSTS`, `HOST_CHECK_MODE`, `DASHBOARD_BIND_ADDR`). `RECON_WRITE_OPEN=1` skips the token and is only for a trusted private network, never a public hostname.
4. **Dry run**: `docker exec <name> school-state assignments migrate --dry-run` (read-only; read the counts and any refusal reason).
5. **Migrate**: `docker exec <name> school-state assignments migrate`. It copies legacy items and action items into assignments and writes a backup first. Exit 3 means maintenance is off or a sync is running.
6. `school-state transition maintenance off`.
7. **Turn the ingest on**: `RECON_INGEST_ENABLED=1` in `config/env`, `docker compose up -d`.
8. **Run a sync**: `docker exec <name> /app/sync/school-sync.sh` (or wait for the next cron). The merge runs at the end of `school-state update`.
9. **Check**: `docker exec <name> school-state assignments report` and `docker exec <name> school-state assignments digest --mode morning` (preview only, sends nothing).
10. **Open `/app/`**: Reconcile shows by itself (`GET /api/v2/config` answers `enabled` and `migrated`). Close and reopen an installed PWA once so it takes the new service worker (cache `school-v4`).
11. **Each device, once**: Settings, section "Assignments": "Who is using this device" (name and Parent or Kid) and "Write token". Both are kept only in that browser (`school-actor`, `school-recon-token`). Without them the action buttons open "Set up this device".
12. **Later**: `RECON_DIGEST_ENABLED=1` (replaces the LLM morning and night digests, see `docs/ops/recon-digest.md`), then, once the digest looks right, `RECON_PUSH_ENABLED=1` (immediate pushes).

## Day one: what to check

- `school-state assignments report` (add `--redact` before pasting it anywhere): merge rate per kid, unmatched pairs, alias suggestions, possible duplicates. A pair that is the same class under two names gets `school-state assignments alias add --kid NAME --from "Alias" --to "Canonical"`; the next sync re-merges.
- A "Possible duplicate" chip means the merge found two candidates and did not guess; note them and compare with the report's unmatched pairs.
- `/api/v2/summary` (or the banner at the top of Reconcile): `ingest_ok` true and no "Not verified" banner after a fresh sync. A source that is overdue shows "Not verified: <source> data is out of date"; a kid with nothing open then reads "Nothing open (not verified)", not "All clear".
- Tap Done on a row, reload, Undo: it must persist and restore.
- Draft note on a Disputed or Missing row: it is only a draft (copy, open in your mail app, open in portal). The server never sends mail. Teacher name and email come from the class settings.
- `school-state watchdog --dry-run`: no `recon_ingest` problem is reported.

## Rollback

No image rollback is needed.

1. Set `RECON_INGEST_ENABLED=0` (and `RECON_DIGEST_ENABLED=0`, `RECON_PUSH_ENABLED=0` if set) in `config/env`; `docker compose up -d`.
2. `/app/` shows Today again on the next load (the config answers 404, no error is shown). `/today` and `/classic/*` are always available, and `/recon` then says it is not switched on.
3. v1 data and digests were never changed. The v2 tables and their data stay in `school.db`; keep them for a later retry or dump them with `docker exec <name> school-state assignments export > recon-export.json` (contains titles; keep it private).
4. To go live again, set the flag back to `1` and run a sync; no new migration is needed.

See also `docs/ops/spa-redesign-rollout.md` (SPA deploy, sync token), `docs/ops/recon-digest.md`, `docs/ops/host-allowlist-and-port-binding.md`.
