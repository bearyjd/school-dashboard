# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-04-09

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
