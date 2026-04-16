# GitHub Actions Docker Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub Actions workflow that builds and pushes the Docker image to `ghcr.io/bearyjd/school-dashboard` on relevant pushes to `main`, and update `docker-compose.yml` so `.14` can pull it.

**Architecture:** One new workflow file (`.github/workflows/docker.yml`) with a path filter so builds only trigger when image-affecting files change. One line added to `docker-compose.yml` to point compose at the registry image. No secrets beyond the built-in `GITHUB_TOKEN`.

**Tech Stack:** GitHub Actions, Docker Buildx, ghcr.io (GitHub Container Registry).

---

### Task 1: Create the GitHub Actions workflow

**Files:**
- Create: `.github/workflows/docker.yml`

No automated test is possible for a CI workflow. Verification is done by pushing to `main` and checking the Actions tab at `https://github.com/bearyjd/school-dashboard/actions`.

---

- [ ] **Step 1: Create the workflows directory**

```bash
mkdir -p .github/workflows
```

---

- [ ] **Step 2: Write `.github/workflows/docker.yml`**

Create the file with this exact content:

```yaml
name: Build and push Docker image

on:
  push:
    branches:
      - main
    paths:
      - Dockerfile
      - pyproject.toml
      - "vendor/**"
      - "school_dashboard/**"
      - "docker/**"
      - school-sync.sh

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          submodules: recursive

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/bearyjd/school-dashboard
          tags: |
            type=raw,value=latest
            type=sha,format=long

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
```

---

- [ ] **Step 3: Verify YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/docker.yml'))" && echo "YAML OK"
```

Expected: `YAML OK`

---

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/docker.yml
git commit -m "ci: add GitHub Actions workflow to build and push Docker image to ghcr.io

- triggers on push to main for Dockerfile, pyproject.toml, vendor/,
  school_dashboard/, docker/, school-sync.sh changes only
- pushes to ghcr.io/bearyjd/school-dashboard with :latest and :sha tags
- uses built-in GITHUB_TOKEN, no extra secrets needed"
```

---

### Task 2: Add image reference to docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

---

- [ ] **Step 1: Add the `image:` line to docker-compose.yml**

Find this block in `docker-compose.yml`:

```yaml
services:
  dashboard:
    build: .
    restart: unless-stopped
```

Replace with:

```yaml
services:
  dashboard:
    image: ghcr.io/bearyjd/school-dashboard:latest
    build: .
    restart: unless-stopped
```

The `build: .` line stays so local builds still work. When both `image:` and `build:` are present, `docker compose pull` fetches from the registry and `docker compose up -d` uses the pulled image. `docker compose up -d --build` still builds locally.

---

- [ ] **Step 2: Verify the file looks right**

```bash
head -6 docker-compose.yml
```

Expected output:
```
services:
  dashboard:
    image: ghcr.io/bearyjd/school-dashboard:latest
    build: .
    restart: unless-stopped
    ports:
```

---

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: add ghcr.io image reference to docker-compose.yml

enables docker compose pull on .14 to fetch pre-built image
instead of building locally"
```

---

### Task 3: Push and verify the workflow runs

---

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

---

- [ ] **Step 2: Watch the workflow run**

Open: `https://github.com/bearyjd/school-dashboard/actions`

The workflow `Build and push Docker image` should appear. First run takes ~8-12 minutes (Playwright/Chromium download). Subsequent runs with layer caching are faster.

Wait for a green checkmark.

---

- [ ] **Step 3: Confirm the image is published**

Open: `https://github.com/bearyjd/school-dashboard/pkgs/container/school-dashboard`

You should see the package with `latest` and a SHA tag listed.

---

- [ ] **Step 4: Test pulling on .14**

SSH into `.14` and run:

```bash
cd /opt/school/dashboard
docker compose pull
docker compose up -d
```

Expected: compose pulls `ghcr.io/bearyjd/school-dashboard:latest` and restarts the container with the new image.

Verify the dashboard is still up:

```bash
curl -s http://localhost:5000 | head -5
```

Expected: HTML starting with `<!DOCTYPE html>` or `<html`.
