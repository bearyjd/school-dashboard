# Evening Email Digest — OpenClaw Cron Prompt

**Schedule:** `0 18 * * *` (6pm daily, America/New_York)
**Model:** Haiku
**Timeout:** 120s
**Delivery:** Signal to YOUR_PHONE

## Prompt

```
Run: GOG_KEYRING_PASSWORD= gog gmail search "in:inbox newer_than:6h" --max 15 -a YOUR_EMAIL -j

If fewer than 3 emails match and none are from YOUR_SCHOOL_DOMAIN or are starred, reply only: "No evening updates." and stop.

For qualifying emails (YOUR_SCHOOL_DOMAIN, starred, or school-activity keywords in subject), fetch body:
  GOG_KEYRING_PASSWORD= gog gmail get <messageId> -a YOUR_EMAIL -j
Extract facts immediately, discard body.

For each action item found, run:
  school-state action add "<child>" "<summary>" --due "<YYYY-MM-DD>" --source email

Then run: school-state html

Produce a brief evening digest. Only include emails received since noon. Skip anything that can wait until tomorrow.

⚡ ACTION REQUIRED
<response needed, signature, deadline — note which child>

🏫 SCHOOL & ACTIVITIES
<school/activity updates only>

Skip promotions, marketing, GitHub. Terse. One line per item. Under 500 chars.
```
