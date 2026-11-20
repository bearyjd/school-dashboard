# Recon digest (morning and night, no LLM)

`RECON_DIGEST_ENABLED=1` replaces the LLM-written Morning Briefing (07:00) and Night Prep (20:30) with a
deterministic text built from the v2 assignments data. Schedules, titles, the deep link and the `digests` row are the
same, so the watchdog's digest-landing check keeps working. The afternoon and weekly digests are never touched.

## What changes when the flag is on

- Per kid: Do tonight (morning: Due next), Waiting on teacher, Needs action (Disputed, Missing, Done not received),
  plus IXL remaining counts. Empty buckets are omitted; at most 6 lines per bucket, then `+N more`; the whole text is
  kept under 3500 bytes (the cap steps down 6 -> 3 -> 1, then counts only; no kid is ever dropped).
- Flags are computed on read at the digest time, so a Friday item is no longer "Do tonight" on Saturday.
- `Data as of ...` shows the last good merge. When the merge failed or a source is stale the text starts with
  `NOT VERIFIED` and names the sources; a kid with nothing open then reads `nothing open (not verified)`, never
  `all clear`.
- Each digest run first recomputes stored flags per kid (a busy kid is skipped and counted in a final `Notes:` line),
  then flushes the push outbox best-effort.
- `LITELLM_URL` is not needed. `NTFY_TOPIC` still is.
- If the v2 build fails (not migrated, database error) the existing fallback sends the basic homework check with the
  note "Assignment list unavailable", and the command exits 1 so the cron wrapper alerts.

## Prerequisites

1. `school-state assignments migrate` done (maintenance mode on), `RECON_INGEST_ENABLED=1`.
2. Shadow ingest healthy: `school-state assignments report` shows a recent successful merge and a baseline.

## Preview (reads only: no recompute, no LLM, nothing sent or written)

    school-state assignments digest --mode night
    school-state assignments digest --mode morning --now 2026-11-21T07:00:00-05:00 --json

## Turn on, in order

1. Preview both modes for a few days and compare with the portals.
2. Set `RECON_DIGEST_ENABLED=1` in `config/env`; restart the container (the next cron picks it up).
3. Optional: `RECON_DIGEST_LLM=1` adds up to 400 characters of AI summary after the list, under the line
   "AI summary (may be wrong; the list above is the source)". The prompt is `RECON_SUMMARY_PROMPT` in
   `school_dashboard/assignments/digest_run.py`. Any failure, timeout, empty or over-long answer omits it; the list
   above it is identical either way.
4. Optional: `RECON_PUSH_ENABLED=1` for immediate pushes (below).

## Rollback

Set `RECON_DIGEST_ENABLED=0` (and restart). The v1 digests resume with no other change.

## Immediate pushes (`RECON_PUSH_ENABLED`)

Independent of the digest flag. Transitions to Missing or Disputed enqueue one push per assignment and flag, with a
6 hour debounce. Nothing is sent until the baseline run has completed (baseline silence). Pushes are grouped per kid.
Delivery is at-least-once: a crash between the POST and the `sent` mark sends those rows again. Each digest cron
flushes the outbox, which is the retry point for pushes that failed earlier; rows older than 24 hours are dropped and
rows are abandoned after 5 failed attempts.
