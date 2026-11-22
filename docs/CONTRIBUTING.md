# Contributing

## Prerequisites

- Python 3.15 and Node.js 26 to match CI
- Docker and Docker Compose for container testing
- Chromium for the Playwright browser suite

## Setup

```bash
git clone --recurse-submodules https://github.com/bearyjd/school-dashboard.git
cd school-dashboard
git submodule update --init --recursive
```

## Running Tests

Follow the [Development and CI commands](../README.md#development-and-ci) to
reproduce each job. Ordinary tests use synthetic input and local fixtures. Live
scraper tests require explicit opt-in and separate credentials.

Use the **CI gate** summary in the PR’s Actions run to find the first failed step,
job logs, and any Playwright report. The [operator runbook](RUNBOOK.md#pipeline-failure-map)
covers image publishing and deployment failures.

## Project Structure

| Path | Purpose |
|------|---------|
| `school_dashboard/` | Core Python package |
| `web/` | Flask app, Jinja2 templates, and React SPA |
| `sync/school-sync.sh` | Scraper orchestration and state refresh |
| `Dockerfile`, `docker/` | Image build, entrypoint, and cron schedule |
| `vendor/` | Pinned IXL, Schoology, GameChanger, and PowerSchool submodules |
| `state/` | Runtime data (gitignored) |
| `config/env` | Secrets (gitignored) |

## Environment Variables

For a configured local deployment, copy `.env.example` to `config/env` and fill
the variables for the features you use. CI fixtures require no account secrets.

## Docker Build

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

The container image includes the templates, SPA, and Python source. Rebuild and
recreate the container to pick up changes. The production deploy script pulls the
published image.

## Submodule Updates

```bash
git submodule update --remote vendor/ixl-scrape
git add vendor/ixl-scrape
git commit -m "chore: update IXL scraper"
```

Update the dependency lock if the new revision changes dependencies. CI and the
Docker build use the gitlinks committed in the PR.

## Commit Style

```
feat: add X
fix: Y was broken
chore: update scrapers
```
