# Afternoon School Update — OpenClaw Cron Prompt

**Schedule:** `0 14 * * 0-5` (2pm weekdays + Sunday, America/New_York)
**Model:** Haiku
**Timeout:** 60s
**Delivery:** Signal to YOUR_PHONE

**Replaces:** Both "IXL Daily Report" and "School Homework Briefing" crons.

## Prompt

```
Read the school state file and format a combined afternoon update.

Run: school-state show

The state file contains all children, their IXL progress, and Schoology assignments/grades.

Format for Signal:

📚 School Update — [today's date]

IXL:
For each child, report done/assigned and remaining per subject.
If a child has no IXL data or all remaining = 0 → "✅ All clear"
If everyone is caught up → "🎉 Everyone's caught up!"

HOMEWORK:
🚨 DUE TOMORROW — assignments due tomorrow, one line each with child name
📅 DUE THIS WEEK — everything else due in 7 days, grouped by child
Skip section if empty.

GRADES:
List grades per child per course. Flag anything below B with ⚠️
Skip courses with no grade posted.

End with: "X assignments due this week across Y children."

Rules:
- Report numbers verbatim from state file
- Never editorialize
- Be terse. One line per item.
- Under 800 chars total.
- Skip empty sections entirely.
```
