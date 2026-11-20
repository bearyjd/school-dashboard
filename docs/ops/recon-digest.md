# Recon digest (morning and night, no LLM)

`RECON_DIGEST_ENABLED=1` replaces the LLM-written Morning Briefing (07:00) and Night Prep (20:30) with a
deterministic text built from the v2 assignments data. Schedules, titles, the deep link and the `digests` row are the
same, so the watchdog's digest-landing check keeps working. The afternoon and weekly digests are never touched.

## What changes when the flag is on

- Per kid: Do tonight (morning: Due next), Waiting on teacher, Needs action (Disputed, Missing, Done not received).
  Those headings count and list assignments only. IXL has its own last line per kid, subjects in alphabetical order:
  `IXL: ELA 2 remaining, Math 4 remaining`. A subject with 0 left is omitted; one whose status has no leading number
  shows as `<Subject> status unknown`. A kid whose only open thing is IXL shows the name and that line. A kid is
  "all clear" only with no bucket rows and no IXL line.
- Empty buckets are omitted; at most 6 lines per bucket, then `+N more` (counted over the same rows as the heading);
  the deterministic text is kept under 3500 bytes (the cap steps down 6 -> 3 -> 1, then one counts line per kid; if
  even that is too long the tail becomes `+N more kids`; the header lines are never dropped).
- Header: `Data as of Wed Nov 18, 4:05 PM` (always with the date). When the merge is healthy but no assignment row exists
  for any kid the header adds `No assignments on record yet.` and each kid reads `nothing on record`.
- Cards: at most 30, handed out round robin across kids in roster order.
- Flags are computed on read at the digest time, so a Friday item is no longer "Do tonight" on Saturday.
- When the merge failed or a source is stale the text starts with `NOT VERIFIED` and names the sources; a kid with
  nothing open then reads `nothing open (not verified)`, never `all clear`.
- Run order: recompute flags per kid (a busy kid is skipped and counted in a final `Notes:` line), build, push the
  digest, and only then flush the push outbox (best effort, never raises). Optional work never delays the push.
- The text handed to ntfy (list + optional AI paragraph + Notes line) is at most 3900 UTF-8 bytes; the AI paragraph is
  dropped first when it would not fit; the Notes line is always kept.
- `LITELLM_URL` is not needed unless `RECON_DIGEST_LLM` is on. `NTFY_TOPIC` always is.
- If the v2 build fails (not migrated, database error) the existing fallback sends the basic homework check with the
  note "Assignment list unavailable" and the command exits 1. `sync/run-digest.sh` has no overall timeout and, on that
  exit, also sends its own FAILED push, so a v2 build failure produces the v1 quick check AND the wrapper's FAILED
  push (v1 already does the same for LLM failures).
- A broken recon package (the flag cannot be evaluated) logs one ERROR with the exception type and the v1 digest runs.

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
   "AI summary (may be wrong; the list above is the source)". Keep it off unless wanted: it can only be dropped,
   never alters the list or the paragraph. The call has a 10 second wall-clock deadline, so the push is delayed by at
   most about 10 seconds. The paragraph is dropped (one WARNING naming only the rule) when: the digest carries the
   NOT VERIFIED banner (no call is made); the call fails, times out (`deadline`), is empty or longer than 400
   characters; it contains a URL-like token or `@`; a number that is not in the list; a capitalised word of 3+
   letters (not at a sentence start) that is not in the list; a reassurance phrase ("all clear", "nothing due",
   "caught up", ...) while any kid has open rows; more than two sentences; or the final text would exceed 3900 bytes.
   Any literal `<assignment_list>` tag in the data is removed before the list is wrapped for the model. The prompt is
   `RECON_SUMMARY_PROMPT` in `school_dashboard/assignments/digest_run.py`.
4. Optional: `RECON_PUSH_ENABLED=1` for immediate pushes (below).

## Rollback

Set `RECON_DIGEST_ENABLED=0` (and restart). The v1 digests resume with no other change.

## Immediate pushes (`RECON_PUSH_ENABLED`)

Independent of the digest flag. Transitions to Missing or Disputed enqueue one push per assignment and flag, with a
6 hour debounce. Nothing is sent until the baseline run has completed (baseline silence). Pushes are grouped per kid.
Delivery is at-least-once: a crash between the POST and the `sent` mark sends those rows again. Each recon digest cron
flushes the outbox after the digest push, which is the retry point for pushes that failed earlier; rows older than 24 hours are dropped and
rows are abandoned after 5 failed attempts.
