# Host allowlist, port binding and the recon write token

## Settings

| Variable | Where | Default | Effect |
|----------|-------|---------|--------|
| `ALLOWED_HOSTS` | `config/env` | empty | Comma/space separated Host values. Port is ignored, case and a trailing dot are ignored. `.example.net` matches `a.example.net` but not `example.net` or `badexample.net`. Invalid entries (`*`, `/`, `@`, non-ASCII, over 253 characters) are ignored and counted in one WARNING. |
| `HOST_CHECK_MODE` | `config/env` | `log` | `off`, `log` or `enforce`. Unset, empty or unknown means `log`. |
| `DASHBOARD_BIND_ADDR` | `.env` beside `docker-compose.yml` | `127.0.0.1` | Host interface Docker publishes the port on. |
| `DASHBOARD_PORT` | `.env` beside `docker-compose.yml` | `5000` | Host port. The container port stays 5000. |

Compose reads the `.env` file next to `docker-compose.yml` for `${...}` interpolation. `config/env` only feeds the container, so the two bind variables have no effect there.

Always allowed: `localhost`, `127.0.0.1`, `[::1]` and the host of the public dashboard URL (`digest.DASHBOARD_BASE`). The container healthcheck calls `127.0.0.1:5000` and `scripts/smoke-live.sh` defaults to `localhost:5000`, so both pass without any entry.

## Modes

- `off`: no check.
- `log` (default): an unknown Host is served and logged as a WARNING, once per distinct host per process (64 hosts at most; values cut to 80 characters; malformed values show as `<malformed>`).
- `enforce`: an unknown or malformed Host gets `421` with `{"error":"host_not_allowed"}` and `Cache-Control: no-store`. The check runs before the maintenance gate and before the recon token check, on every route (v1, v2, `/app/`, `/api/csp-report`).

Lock-out guard: `enforce` with `ALLOWED_HOSTS` unset, empty or all-invalid does NOT reject. It runs as `log` and logs "enforce requested but ALLOWED_HOSTS is empty; running in log-only mode".

## What counts as the Host

Only the raw `Host` header the app receives. `X-Forwarded-Host` is never read. The reverse proxy must forward the public Host (nginx: `proxy_set_header Host $host;`). If the proxy rewrites Host to `127.0.0.1:5000`, every request passes the check and protection then depends on the proxy alone. Clients that reach the app by IP (LAN or tailnet address) send that IP as Host, so list those IPs (for IPv6 literals use `[addr]`) or they get 421 under `enforce`.

## Port binding and `TRUSTED_PROXY_COUNT`

The repo compose file publishes `127.0.0.1:5000`. On a single host where the proxy connects to `127.0.0.1:5000`, Flask is then reachable only through the proxy, which is the precondition for `TRUSTED_PROXY_COUNT` above 0 (see `docs/ops/spa-redesign-rollout.md`). If the proxy runs on ANOTHER host, set `DASHBOARD_BIND_ADDR` to the interface the proxy reaches (LAN, tailnet or docker-network address) and firewall it so only the proxy can connect. Do not leave it on `0.0.0.0` together with `TRUSTED_PROXY_COUNT` above 0.

## Rollout

1. Before pulling the new compose file on the server, set `DASHBOARD_BIND_ADDR=0.0.0.0` in the `.env` beside the server's `docker-compose.yml`. That is today's effective binding, so nothing changes silently. Then deploy with the defaults (`HOST_CHECK_MODE=log`).
2. Read the logs for a few days: `docker compose logs dashboard | grep "not in ALLOWED_HOSTS"`. Each line is a real Host that reached the app.
3. Put the legitimate hosts (public name, any LAN/tailnet IPs in use) in `ALLOWED_HOSTS`.
4. Set `HOST_CHECK_MODE=enforce` and `docker compose up -d`.
5. Verify (replace the placeholders):
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: school.example.net' http://127.0.0.1:5000/api/sync/status   # listed: 200
   curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: unlisted.invalid'   http://127.0.0.1:5000/api/sync/status   # 421
   BASE_URL=https://school.example.net scripts/smoke-live.sh --container <name>
   ```
   When `BASE_URL`'s host is not allowed, the smoke script prints a 421 hint. Add that host to `ALLOWED_HOSTS`; there is no path exemption.
6. Only then narrow `DASHBOARD_BIND_ADDR` (loopback or the proxy interface).

## Rollback

No image rollback is needed for either change.

- Host check: set `HOST_CHECK_MODE=off` (or `log`) in `config/env` and `docker compose up -d`.
- Port binding: set `DASHBOARD_BIND_ADDR=0.0.0.0` in the compose-side `.env` and `docker compose up -d`.

## Recon write token

`/api/v2` writes need the header `X-Recon-Token` equal to `RECON_WRITE_TOKEN` (at least 32 characters; generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`). Reads stay open. `GET /api/v2/config` reports `write_auth` (`token`, `open` or `unconfigured`), `write_token_header` and `write_token_min_length`, never the token.

- `501`: no token configured (unset or shorter than 32 characters); writes are disabled.
- `401`: header missing or wrong.
- `429`: too many wrong tokens from one client (shared limiter with `/api/sync`; a correct token is never blocked).
- `RECON_WRITE_OPEN=1` skips the token. Acceptable only when the app is reachable solely from a private network you trust (loopback or a firewalled tailnet), never on a public hostname.

Each device enters the token once; the SPA keeps it in `localStorage`, like the sync token. Rotate by changing `RECON_WRITE_TOKEN` in `config/env`, restarting, and re-entering it on each device.
