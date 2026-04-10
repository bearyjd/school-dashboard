# Contributing

## Prerequisites

- Python 3.12+
- Docker + Docker Compose (for container testing)
- `playwright install chromium` (for IXL login tests)

## Setup

```bash
git clone --recurse-submodules <repo>
cd school-dashboard
pip install -e ".[server]" -e vendor/ixl-scrape -e vendor/schoology-scrape
playwright install chromium
cp .env.example config/env   # fill in secrets
```

## Running Tests

```bash
pytest                        # all 26 tests
pytest tests/test_db.py -v    # single file
pytest -k "test_name"         # single test
ruff check .                  # lint
```

All tests use mocks — no live credentials or network needed.

## Project Structure

| Path | Purpose |
|------|---------|
| `school_dashboard/` | Core Python package |
| `web/` | Flask app + Jinja2 templates |
| `sync/school-sync.sh` | Cron sync script (IXL → SGY → state → intel → HTML) |
| `docker/` | Dockerfile, entrypoint.sh, crontab |
| `vendor/` | git submodules: ixl-scrape, schoology-scrape |
| `state/` | Runtime data (gitignored) |
| `config/env` | Secrets (gitignored) |

## Environment Variables

Copy `.env.example` to `config/env`. Required: `LITELLM_URL`, `LITELLM_API_KEY`, `LITELLM_MODEL`, `IXL_EMAIL`, `IXL_PASSWORD`, `SGY_EMAIL`, `SGY_PASSWORD`, `SGY_BASE_URL`, `SGY_SCHOOL_NID`, `GOG_ACCOUNT`, `NTFY_TOPIC`, `SCHOOL_EMAIL_ACCOUNT`.

## Docker Build

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

Template changes in `web/` take effect immediately (volume-mounted). Python package changes require `docker compose build`.

## Submodule Updates

```bash
git submodule update --remote vendor/ixl-scrape
git submodule update --remote vendor/schoology-scrape
git add vendor/ && git commit -m "chore: update scrapers"
```

## Commit Style

```
feat: add X
fix: Y was broken
chore: update scrapers
```
