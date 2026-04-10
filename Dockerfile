FROM python:3.12-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    cron \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install gog CLI (Google Workspace CLI)
ARG GOG_VERSION=0.12.0
RUN wget -qO /tmp/gog.tar.gz \
    "https://github.com/steipete/gogcli/releases/download/v${GOG_VERSION}/gogcli_${GOG_VERSION}_linux_amd64.tar.gz" \
    && tar -xzf /tmp/gog.tar.gz -C /usr/local/bin gog \
    && chmod +x /usr/local/bin/gog \
    && rm /tmp/gog.tar.gz

WORKDIR /app

# Install Python deps
COPY pyproject.toml ./
COPY vendor/ixl-scrape/ ./vendor/ixl-scrape/
COPY vendor/schoology-scrape/ ./vendor/schoology-scrape/

RUN pip install --no-cache-dir -e ".[server]" \
    -e "vendor/ixl-scrape[browser]" \
    -e vendor/schoology-scrape

# Install Playwright + Chromium (for IXL login)
RUN playwright install chromium --with-deps

# Copy app source
COPY school_dashboard/ ./school_dashboard/
COPY web/ ./web/
COPY school-sync.sh ./sync/school-sync.sh
COPY config/ ./config/

# Set up cron
COPY docker/crontab /etc/cron.d/school-dashboard
RUN chmod 0644 /etc/cron.d/school-dashboard && crontab /etc/cron.d/school-dashboard

# State directory (mounted as volume)
RUN mkdir -p /app/state

ENV PYTHONPATH=/app
ENV FLASK_APP=web/app.py

EXPOSE 5000

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
