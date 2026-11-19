# python:3.15-slim (multi-arch index digest, resolved 2026-11-09); Renovate bumps the digest
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    cron \
    wget \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# cron reads /etc/localtime, not the TZ env var: without this the schedules in
# docker/crontab fire in UTC (digests 4-5h early) even though TZ=America/New_York.
RUN ln -sf /usr/share/zoneinfo/America/New_York /etc/localtime \
    && echo "America/New_York" > /etc/timezone

# Install gog CLI (Google Workspace CLI)
ARG GOG_VERSION=0.12.0
RUN wget -qO /tmp/gog.tar.gz \
    "https://github.com/steipete/gogcli/releases/download/v${GOG_VERSION}/gogcli_${GOG_VERSION}_linux_amd64.tar.gz" \
    && tar -xzf /tmp/gog.tar.gz -C /usr/local/bin gog \
    && chmod +x /usr/local/bin/gog \
    && rm /tmp/gog.tar.gz

WORKDIR /app

# Install Python deps
COPY pyproject.toml requirements.lock ./
COPY vendor/ixl-scrape/ ./vendor/ixl-scrape/
COPY vendor/schoology-scrape/ ./vendor/schoology-scrape/
COPY vendor/gc/ ./vendor/gc/
COPY vendor/powerschool-scrape/ ./vendor/powerschool-scrape/

# -c pins every transitive dep to requirements.lock (editable installs still work).
RUN pip install --no-cache-dir -c requirements.lock -e ".[server]" \
    -e "vendor/ixl-scrape[browser]" \
    -e vendor/schoology-scrape \
    -e vendor/gc \
    -e "vendor/powerschool-scrape[browser]"

# Install Playwright + Chromium (for IXL login). The playwright package is pinned in
# requirements.lock, so the Chromium build downloaded here always matches it.
RUN playwright install chromium --with-deps

# Install Node.js for SPA build
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Build React SPA
COPY web/spa/package*.json /app/web/spa/
RUN npm --prefix /app/web/spa ci
COPY web/spa/ /app/web/spa/
RUN npm --prefix /app/web/spa run build

# Copy app source
COPY school_dashboard/ ./school_dashboard/
COPY web/ ./web/
COPY sync/ ./sync/
RUN chmod +x /app/sync/*.sh
COPY config/ ./config/

# Set up cron
COPY docker/crontab /etc/cron.d/school-dashboard
RUN chmod 0644 /etc/cron.d/school-dashboard

# State directory (mounted as volume)
RUN mkdir -p /app/state

ENV PYTHONPATH=/app
ENV FLASK_APP=web/app.py

EXPOSE 5000

# Unauthenticated GET, exempt from the maintenance 503 (safe methods are never blocked).
# curl is already installed above; a hung Flask makes this fail and the container goes unhealthy.
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS --noproxy '*' --max-time 8 -o /dev/null http://127.0.0.1:5000/api/sync/status || exit 1

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
