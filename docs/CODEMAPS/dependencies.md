<!-- Generated: 2026-05-05 | Files scanned: 48 | Token estimate: ~700 -->

# Dependencies

## External Services

| Service | Env vars | Used by | Purpose |
|---|---|---|---|
| LiteLLM proxy | `LITELLM_URL`, `LITELLM_API_KEY`, `LITELLM_MODEL` | `digest.py`, `web/app.py` (`/api/chat`, `/api/agent/inline`) | AI synthesis + chat |
| Gmail (via gog) | `GOG_ACCOUNT`, `GOG_KEYRING_PASSWORD` | `email.py`, `vendor/gc` `_fetch_gc_otp`, `gcal.py` | Email digest + GC OTP recovery + Google Calendar |
| ntfy.sh | `NTFY_TOPIC` | `digest.py`, wrapper scripts | Push notifications |
| IXL | `IXL_EMAIL`, `IXL_PASSWORD` | `vendor/ixl-scrape` | Skills + diagnostics |
| Schoology | `SGY_EMAIL`, `SGY_PASSWORD`, `SGY_BASE_URL`, `SGY_SCHOOL_NID` | `vendor/schoology-scrape` | Assignments + grades |
| GameChanger | `GC_EMAIL`, `GC_PASSWORD`, `GC_TOKEN` (optional), `GC_DEVICE_ID` (auto), `GC_TEAM_MAP` | `vendor/gc` | Team schedules |

## Python (`pyproject.toml`)

### Core
| Package | Use |
|---|---|
| flask | Web server + API |
| jinja2 | Dashboard template + SPA index |
| beautifulsoup4 | HTML parsing in vendor scrapers |
| requests | All HTTP |
| pdfminer.six | PDF text extraction |

### Optional
| Extra | Purpose |
|---|---|
| `[server]` | Flask + WSGI deps |

### Vendor (editable installs)
| Path | Adds |
|---|---|
| `vendor/ixl-scrape` | `ixl` CLI (requests + Playwright for login) |
| `vendor/schoology-scrape` | `sgy` CLI (requests + bs4) |
| `vendor/gc` | `gc` CLI (requests + Playwright + auto-OTP via `gog gmail search`) |

## Binaries / OS tools

| Tool | Version | Source | Use |
|---|---|---|---|
| gog (`gogcli`) | v0.12.0 | downloaded in Dockerfile | Gmail + Google Calendar; backs GC auto-OTP |
| Chromium | latest | `playwright install chromium --with-deps` | IXL + GC headless logins |
| Node 20 | distro | apt | SPA build stage |
| cron | distro | apt | scheduled syncs + digests |

## Frontend (web/spa)

| Package | Use |
|---|---|
| react / react-dom | UI |
| vite | dev server + production build |
| typescript | typing |
| marked | markdown rendering in chat |

CDN-loaded `marked.js` v9 still appears in the legacy `/` template; the SPA imports the npm package.

## Git submodules

| Path | Remote | Pinned at |
|---|---|---|
| `vendor/ixl-scrape` | bearyjd/ixl-scrape | tracked by `git submodule update --remote` |
| `vendor/schoology-scrape` | bearyjd/schoology-scrape | tracked by `git submodule update --remote` |
| `vendor/gc` | bearyjd/gc | pinned per-commit; bumped via PR (last: 0.1.30) |

CI does NOT auto-bump `vendor/gc` (only ixl + sgy) so version pin stays explicit.

## Image volumes

```
./state          /app/state            (DB, JSON state files, logs)
./web            /app/web              (template + SPA dist hot-reload)
./config/env     /app/config/env       (read-only)
/root/.config/gogcli  same             (gog OAuth tokens + keyring)
/root/.ixl       same                  (IXL session cache)
/root/.sgy       same                  (Schoology session cache)
/root/.gc        same                  (GC token + Playwright context)
```

## Secrets surface

- `config/env`: gitignored, holds all credentials and `GOG_KEYRING_PASSWORD`. `GC_TOKEN` should not be set here once the headless self-heal is working — it overrides the live `~/.gc/.env`.
- `~/.gc/.env`: holds `GC_PASSWORD` in plaintext (root-owned). Eventual hardening item: move to a secret store.
