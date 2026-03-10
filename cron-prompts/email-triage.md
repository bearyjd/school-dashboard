# Email Triage — OpenClaw Cron Prompt

**Schedule:** `5 6 * * *` (6:05am daily) + `35 14 * * 1-5` (2:35pm weekdays)
**Model:** Haiku
**Timeout:** 60s
**Delivery:** Signal to YOUR_PHONE

**Purpose:** Read the pre-processed email digest, create action items, flag uncertain emails, and send a brief triage summary.

## Prompt

```
Run: school-state email-show

You will see a list of pre-classified emails with snippets. For each non-SKIP email, decide:

- ACTIONABLE: Requires human action (deadline, signature, RSVP, payment, form, reply needed)
- INFORMATIONAL: Worth knowing but no action needed (schedule update, grade posted, event announcement)
- UNCERTAIN: Can't confidently classify — needs human review
- SKIP: Marketing, notifications, or no relevance to family/school

For each ACTIONABLE email found, run:
  school-state action add "<child>" "<summary>" --due "<YYYY-MM-DD>" --source email --type "<permission_slip|event|deadline|payment|reply_needed>"

Rules:
- Pay attention to PDF attachment snippets — school permission slips and flyers often contain the actual deadlines
- If an email has an image attachment from a school domain, note it as "has flyer image — may need review"
- Be conservative: when in doubt, classify as UNCERTAIN rather than SKIP

ALWAYS send a Signal summary. NEVER use HEARTBEAT_OK. ALWAYS reply with this format:

📬 Email Triage — [time of day: Morning or Afternoon]
✅ [N] action items added
❓ [N] need your review:
  • [subject line — reason uncertain]
ℹ️ [N] informational
🗑️ [N] skipped

If there are UNCERTAIN emails, list each on its own line with the subject and why you're unsure.
If zero actionable and zero uncertain, just send: "📬 Inbox clear — [N] skipped, nothing needs attention."
Under 500 chars total.
```
