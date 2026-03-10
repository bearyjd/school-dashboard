# Email Triage — OpenClaw Cron Prompt

**Schedule:** `5 6 * * *` (6:05am daily, after school-sync.sh runs at 6:00)
**Model:** Haiku
**Timeout:** 60s
**Delivery:** none (no Signal notification)

**Purpose:** Read the pre-processed email digest and classify each email so the morning briefing only fetches full bodies for actionable items.

## Prompt

```
Run: school-state email-show

You will see a list of pre-classified emails with snippets. For each non-SKIP email, decide:

- ACTIONABLE: Requires human action (deadline, signature, RSVP, payment, form, reply needed)
- INFORMATIONAL: Worth knowing but no action needed (schedule update, grade posted, event announcement)
- SKIP: Marketing, notifications, or no relevance to family/school

For each ACTIONABLE or INFORMATIONAL email, extract:
- child: Which child this relates to (or "family" if general)
- due_date: Any deadline (YYYY-MM-DD format, or null)
- summary: One-line description of what matters

For each ACTIONABLE email found, run:
  school-state action add "<child>" "<summary>" --due "<YYYY-MM-DD>" --source email --type "<permission_slip|event|deadline|payment|reply_needed>"

If no actionable emails found, do nothing.

Rules:
- Pay attention to PDF attachment snippets — school permission slips and flyers often contain the actual deadlines
- If an email has an image attachment from a school domain, note it as "has flyer image — may need review"
- Be conservative: when in doubt, classify as ACTIONABLE rather than SKIP
- Do NOT output a summary. Just process the action items silently.
```
