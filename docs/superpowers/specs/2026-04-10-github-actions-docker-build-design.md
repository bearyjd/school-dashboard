# GitHub Actions Docker Build — Design Spec

## Overview

Set up GitHub Actions to build the Docker image on push to `main` and push it to GitHub Container Registry (`ghcr.io`). The LXC host at `192.168.1.14` pulls the pre-built image instead of building locally. No external registry accounts or secrets needed — the repo is public so `ghcr.io` packages are publicly pullable.

## What Changes

Two files:

### 1. `.github/workflows/docker.yml` (new)

**Trigger:** Push to `main`, only when files that affect the image change:
- `Dockerfile`
- `pyproject.toml`
- `vendor/**`
- `school_dashboard/**`
- `docker/**`
- `school-sync.sh`

Skipped for: `web/**`, `docs/**`, `*.md`, `tests/**` — these don't change the image (web/ is volume-mounted at runtime).

**Steps:**
1. `actions/checkout@v4` with `submodules: recursive` — vendor/ixl-scrape and vendor/schoology-scrape are git submodules and must be present for the COPY steps in the Dockerfile
2. `docker/login-action` to `ghcr.io` using `${{ secrets.GITHUB_TOKEN }}` (built-in, no configuration needed)
3. `docker/metadata-action` to produce two tags: `latest` and the full git SHA
4. `docker/build-push-action` to build and push both tags

**Image name:** `ghcr.io/bearyjd/school-dashboard`

### 2. `docker-compose.yml` (modify)

Add `image: ghcr.io/bearyjd/school-dashboard:latest` to the `dashboard` service. The existing `build: .` line stays so local builds still work. When both are present, `docker compose pull` fetches the registry image and `docker compose up -d` uses it; `docker compose up -d --build` still builds locally.

## Deployment Workflow on `.14` After This

```bash
# Pull latest image and restart (replaces rsync + remote build)
docker compose pull && docker compose up -d
```

No auth required — public repo, public ghcr.io package.

## What Does NOT Change

- Local dev workflow unchanged (`docker compose up -d --build` still works)
- `web/` volume mount unchanged — template changes still deploy with restart only
- No secrets to configure beyond the built-in `GITHUB_TOKEN`
- No Docker Hub account needed
