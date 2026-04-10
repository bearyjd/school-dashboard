<!-- Generated: 2026-04-10 | Files scanned: 34 | Token estimate: ~400 -->

# Dependencies

## External Services

| Service | Env Var | Purpose |
|---------|---------|---------|
| LiteLLM proxy | `LITELLM_URL` | AI: intel extraction, digest generation, chat |
| Gmail (via gog) | `GOG_ACCOUNT` | Email fetch for intel + digest context |
| ntfy.sh | `NTFY_TOPIC` | Morning push notification delivery |
| IXL | `IXL_EMAIL/PASSWORD` | Student performance data |
| Schoology | `SGY_EMAIL/PASSWORD` | Assignments, grades, announcements |

## Python Dependencies

### Core (`pyproject.toml`)
| Package | Version | Use |
|---------|---------|-----|
| jinja2 | >=3.1 | Dashboard HTML rendering |
| beautifulsoup4 | >=4.12 | HTML parsing (SGY, email) |
| pdfminer.six | >=20221105 | PDF text extraction (calendar, attachments) |

### Server optional (`[server]`)
| Package | Version | Use |
|---------|---------|-----|
| flask | >=3.0 | Web server + chat API |

### Vendor: ixl-scrape
| Package | Use |
|---------|-----|
| requests | IXL API calls |
| playwright | Headless Chromium for IXL login (Cloudflare bypass) |

### Vendor: schoology-scrape
| Package | Use |
|---------|-----|
| requests | Schoology HTTP |
| beautifulsoup4 | HTML parsing |

## Binaries / Tools

| Tool | Version | Install | Use |
|------|---------|---------|-----|
| gog (gogcli) | v0.12.0 | Downloaded in Dockerfile from GitHub releases | Google OAuth CLI — Gmail fetch |
| Chromium | latest | `playwright install chromium --with-deps` | IXL login |
| cron | system | `apt-get install cron` (in Dockerfile) | Scheduled syncs |

## Git Submodules

| Path | Remote | Role |
|------|--------|------|
| `vendor/ixl-scrape` | your ixl-scrape repo | IXL scraper (pip install -e) |
| `vendor/schoology-scrape` | your schoology-scrape repo | Schoology scraper (pip install -e) |

## Frontend

| Asset | Version | CDN | Use |
|-------|---------|-----|-----|
| marked.js | v9 | jsDelivr | Markdown → HTML in chat UI |
