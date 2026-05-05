# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- React SPA frontend with responsive multi-screen layout (Home, Child, Sync, Chat tabs)
- Inline AI agent component for contextual LLM interactions on homework items and sync sources
- On-demand sync endpoint with per-source trigger buttons and status polling
- Per-source data freshness tracking and display in UI
- GameChanger team schedule integration with event parsing in digest
- PWA manifest, service worker, and offline fallback support
- Android Trusted Web Activity (TWA) domain verification route
- Digest carousel with deep-linked cards for interactive task management
- Weekly digest (Friday/Sunday) in addition to daily morning/afternoon/night schedules
- Node.js SPA build step to Docker image with Vite + React bundling
- **Self-healing GameChanger sync** at three layers (vendor/gc 0.1.30+):
  - Layer 1: `GCClient._get` retries any 401 once after a transparent
    session refresh via the saved Playwright context, persisting the
    fresh user JWT to `~/.gc/.env` for cron and `/api/sync` to pick up.
  - Layer 2: `sync/gc-scrape.sh` sums events across all teams; if every
    team came back empty (the 401-silent-fail signature), exits with a
    loud ntfy alert instead of overwriting `gc-schedule.json`.
  - Layer 3: `sync/school-sync.sh` calls `gc token-refresh` upfront so
    each cron run starts with a hot user JWT.
- **Auto-OTP via gog** — `gc token-refresh` and `gc summary` recover
  fully headless from a dead token: Playwright re-auth, Gmail OTP fetch
  via `gog gmail search`, automatic submission, fresh JWT capture.

### Changed
- Flask now serves React SPA at `/app` route with `__SYNC_TOKEN__` injection
- Digest system stores cards in database for carousel navigation and state persistence
- Sync metadata persists per-source timestamps to JSON file for freshness calculation
- `gc token-refresh` no longer requires `--visible` to fall through to a
  full Playwright login. Headless cron calls now run the complete
  email + password + auto-OTP flow when the saved context is unusable.

### Fixed
- `PYTHONPATH=/app` now set in crontab — cron does not inherit Docker `ENV` variables
- XSS vulnerability in sync token injection via HTML entity escaping
- Wrapper script crash on unset `SCHOOL_STATE_PATH` with `set -u` enabled
- Dockerfile now copies entire `sync/` directory (was only copying `school-sync.sh`)
- Stale chat history and silent polling errors in SPA
- Digest deep-link URL safety and source sanitization
- `gc-schedule.json` no longer silently goes stale: `gc summary` used to
  return 200 with empty data on 401, `gc-scrape.sh` treated that as
  success, and `sync_meta.json` reported `gc.last_result: ok` while
  `state/gc-schedule.json` sat 3 weeks old. All three layers above
  close that loop.
- GameChanger token capture now filters JWTs by `payload.type == "user"`
  before persisting. The SPA bootstrap fires a `type=client` device
  JWT (10-min TTL) before the user JWT lands; previous capture path
  stored whichever arrived first and 401'd every `/me/teams` call.
- localStorage fallback now collects all JWT candidates and lets Python
  pick the first user-type one. Previously returned the first `eyJ...`
  match (often the client token), which the type filter rejected
  without ever looking further.
- `_playwright_login` headless OTP selector now includes `input#code` /
  `input[name="code"]` to match GameChanger's actual OTP input. The
  generic `autocomplete="one-time-code"` / `type="tel"` /
  `inputmode="numeric"` selectors did not match.
- `_fetch_gc_otp` rejects stale OTP emails — codes from prior
  Sign-in clicks within `newer_than:5m` are skipped (filtered by
  message timestamp >= function-start with 60-s slack), so the SPA
  no longer silently rejects an old code.

## [1.0.0] - 2026-04-10

### Added
- All-in-one Docker deployment (`Dockerfile`, `docker-compose.yml`, `docker/entrypoint.sh`, `docker/crontab`)
- `vendor/ixl-scrape` and `vendor/schoology-scrape` as git submodules
- `gog` v0.12.0 binary bundled in Docker image for Google OAuth / Gmail access
- SQLite event store (`school.db`) with `events` table and INSERT OR IGNORE dedup
- `calendar_import.py` — parse school calendar PDF into events DB via pypdf; detects spaced month headers, classifies 10 event types
- `intel.py` — LiteLLM-powered email intel extraction; inserts dated events and recurring facts from classified emails
- `digest.py` — morning digest generation via LiteLLM; push delivery via ntfy.sh
- `facts.json` — long-term family memory extracted from emails
- Flask web app (`web/app.py`) with streaming `/api/chat` endpoint using 30-day events + facts + state as system context
- Chat UI with `marked.js` markdown rendering for bot responses
- `school-sync.sh` cron orchestration: IXL → SGY → state → email-sync → intel → HTML → digest
- `.env.example` template covering all required environment variables
- `CLAUDE.md` developer guide for the all-in-one repo
- `docs/CODEMAPS/` — token-lean architecture documentation (5 files)
- LiteLLM as external dependency (point to any running instance via `LITELLM_URL`)

### Changed
- Restructured from multi-repo OpenClaw integration to standalone all-in-one Docker repo
- State file paths default to `state/` directory (overridable via env vars)
- Notifications moved from Signal to ntfy.sh push

### Removed
- OpenClaw server dependency (LXC install scripts, Signal integration)
- `/etc/school-dashboard/config.json` config file pattern (replaced by `config/env`)
