# School Morning Digest — Requirements Spec

## Overview

A daily automated system that scrapes Schoology (grades + assignments) and IXL (progress/scores), optionally extracts intelligence from emails via LLM, synthesizes a digest using LiteLLM/openclaw, and pushes a Signal message every morning to two phones (one iPhone, one Android). Runs unattended on an always-on home Debian/Ubuntu Linux machine.

---

## Key Themes

### Notification delivery
Signal is the primary channel, reaching both an iPhone and Android number. Email is an acceptable fallback if Signal delivery fails or during initial setup. The message must be short enough to read at a glance — a parent shouldn't have to open an app to get the gist.

### Message content
The morning digest covers:
- **Grades from Schoology** — recent grade changes and current standing per class
- **Upcoming assignments** — what's due today and this week
- **IXL progress** — skill scores or time practiced from the previous day
- **Email intelligence** — optionally, items scraped or summarized from school-related emails via LLM
- All of the above formatted as a single cohesive summary, not raw data dumps

### LLM synthesis
The digest is not a raw data export. openclaw (calling LiteLLM) synthesizes the scraped data into natural language. The LiteLLM container is already running in this environment; the school digest pipeline just needs to call it.

### Runtime environment
The system runs on the always-on home Debian/Ubuntu machine, not on the Fedora dev laptop. The scrapers (Playwright-based Python CLIs) and signal-cli run there. A Docker Compose stack is the right approach — one compose file that wires together:
- scraper service (Python + Playwright/Chromium)
- signal-cli service (Java, linked to a dedicated Signal number)
- LiteLLM/openclaw (already running, referenced by URL)
- Optional: lightweight Flask dashboard for viewing history over LAN/Tailscale

### Current tool state
Neither scraper has been verified working yet — the first milestone is getting `schoology-scrape` and `ixl-scrape` running correctly on the Fedora dev box, then containerizing them for the home machine.

---

## Decisions & Positions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Notification channel | Signal (primary), email (fallback) | Cross-platform, already in use elsewhere in the stack |
| Runtime host | Always-on home Debian/Ubuntu box | Reliable cron, full Linux, Playwright works natively |
| Deployment method | Docker Compose | Isolates deps (Playwright, Java for signal-cli), portable |
| LLM synthesis | LiteLLM via openclaw | Already running; avoids duplicate infra |
| Scheduling | cron inside the scraper container | Simple, auditable, no extra scheduler service needed |
| Signal delivery | signal-cli with dedicated bot number | Registers once, delivers to any Signal number including both phones |

---

## Architecture

```
[cron @ 7am]
    │
    ▼
[scraper container]
    ├── schoology-scrape CLI → grades, assignments
    ├── ixl-scrape CLI      → IXL scores
    └── email scraper       → raw email text
    │
    ▼
[LiteLLM / openclaw]  ← synthesize digest text
    │
    ▼
[signal-cli container]
    ├── send to Wife's number (iPhone)
    └── send to your number (Android)
```

---

## Milestones

### 0. Verify scrapers work on dev box
- Get `schoology-scrape` and `ixl-scrape` running locally with real credentials
- Confirm Playwright/Chromium launches, logins succeed, data is returned

### 1. Containerize scrapers
- Dockerfile for scraper service: Python 3.12 + Playwright + Chromium
- `.env` file for credentials (Schoology login, IXL login)
- Manual test: `docker run --env-file .env scraper schoology` returns data

### 2. Wire LiteLLM synthesis
- POST scraped data to LiteLLM endpoint (openclaw)
- Prompt: produce a concise morning digest in plain text
- Output: single string ready to send

### 3. Set up signal-cli
- Register a dedicated Signal number (can use a VoIP number like JMP.chat or Google Voice)
- Link signal-cli container to that number
- Test: `signal-cli send -m "test" +1XXXXXXXXXX`
- Add both recipient numbers to config

### 4. Wire cron → scrape → LLM → Signal
- Shell script or Python orchestrator that runs all steps in order
- Cron entry: `0 7 * * 1-5` (weekdays at 7am, adjust as needed)
- Error handling: if scraper fails, send a Signal error ping rather than silence

### 5. Optional: LAN dashboard
- Flask app that serves last N days of digest history
- Accessible over Tailscale or home LAN
- No auth needed if Tailscale-only

---

## Open Questions

- **Signal number source**: Will you use a VoIP number, or link an existing phone number to signal-cli? (VoIP is cleaner — keeps the bot separate.)
- **Email scraper**: What email source? Gmail IMAP, a forwarding address, or something else? This is the most undefined piece.
- **IXL login type**: Does IXL use a username/password or SSO through the school? SSO would complicate the Playwright flow.
- **Schoology login**: Same question — direct login or school SSO/SAML?
- **Cron timing**: 7am was assumed. What time should the digest arrive?
- **Multiple kids?**: Are the scrapers tracking one student or multiple? IXL in particular has per-student accounts.

---

## Constraints & Boundaries

- **Not** a real-time alerting system — once-daily digest only
- **Not** running on Termux/Android directly — phone is receive-only
- **Not** storing scraped data long-term (beyond last few days for dashboard history)
- **Not** using a cloud scraping service — all scraping runs locally to avoid exposing credentials
- Credentials stay in `.env` files on the home machine, never committed to git
