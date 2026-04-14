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

### Changed
- Flask now serves React SPA at `/app` route with `__SYNC_TOKEN__` injection
- Digest system stores cards in database for carousel navigation and state persistence
- Sync metadata persists per-source timestamps to JSON file for freshness calculation

### Fixed
- `PYTHONPATH=/app` now set in crontab — cron does not inherit Docker `ENV` variables
- XSS vulnerability in sync token injection via HTML entity escaping
- Wrapper script crash on unset `SCHOOL_STATE_PATH` with `set -u` enabled
- Dockerfile now copies entire `sync/` directory (was only copying `school-sync.sh`)
- Stale chat history and silent polling errors in SPA
- Digest deep-link URL safety and source sanitization

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
