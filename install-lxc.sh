#!/usr/bin/env bash
# install-lxc.sh — Install school-dashboard on OpenClaw LXC
#
#   ssh root@192.168.1.14 'bash -s' < install-lxc.sh
#
#   — or —
#
#   scp install-lxc.sh root@192.168.1.14:/tmp/
#   ssh root@192.168.1.14 bash /tmp/install-lxc.sh

set -euo pipefail

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ---------------------------------------------------------------
# 1. Install school-dashboard CLI from GitHub
# ---------------------------------------------------------------
if command -v school-state &>/dev/null; then
    log "school-state CLI already installed: $(which school-state)"
    log "Upgrading..."
    pip install --upgrade git+https://github.com/bearyjd/school-dashboard --break-system-packages -q
else
    log "Installing school-dashboard CLI..."
    pip install git+https://github.com/bearyjd/school-dashboard --break-system-packages -q
fi

# ---------------------------------------------------------------
# 2. Clone repo for school-sync.sh (pip doesn't include it)
# ---------------------------------------------------------------
if [[ -d /opt/school-dashboard/.git ]]; then
    log "Repo already at /opt/school-dashboard — pulling latest"
    git -C /opt/school-dashboard pull --ff-only -q
else
    log "Cloning repo to /opt/school-dashboard..."
    git clone -q https://github.com/bearyjd/school-dashboard.git /opt/school-dashboard
fi
chmod +x /opt/school-dashboard/school-sync.sh

# ---------------------------------------------------------------
# 3. Create state directory + config
# ---------------------------------------------------------------
mkdir -p /var/lib/openclaw
mkdir -p /etc/school-dashboard

CONFIG=/etc/school-dashboard/config.json
if [[ -f "$CONFIG" ]]; then
    log "Config already exists at $CONFIG — not overwriting"
else
    log "Creating config at $CONFIG"
    cat > "$CONFIG" << 'CONF'
{
  "children": {
    "Ford": {"grade": "2nd", "school": "SMCS"},
    "Jack": {"grade": "7th", "school": "SMCS"},
    "Pennington": {"grade": "5th", "school": "SMCS"}
  },
  "name_aliases": {
    "ford": "Ford",
    "jack": "Jack",
    "penn": "Pennington",
    "pennington": "Pennington"
  }
}
CONF
    chmod 600 "$CONFIG"
fi

# ---------------------------------------------------------------
# 4. Add 6am system cron (data refresh, no LLM)
# ---------------------------------------------------------------
CRON_LINE="0 6 * * * /opt/school-dashboard/school-sync.sh 2>/tmp/school-sync.log"
if crontab -l 2>/dev/null | grep -qF "school-sync.sh"; then
    log "Cron entry already exists — replacing"
    (crontab -l 2>/dev/null | grep -vF "school-sync.sh" || true; echo "$CRON_LINE") | crontab -
else
    log "Adding 6am cron entry"
    (crontab -l 2>/dev/null || true; echo "$CRON_LINE") | crontab -
fi

# ---------------------------------------------------------------
# 5. Run initial sync (populate state + dashboard)
# ---------------------------------------------------------------
log "Running initial data sync..."
if /opt/school-dashboard/school-sync.sh 2>&1; then
    log "Initial sync PASSED"
else
    log "WARN: Initial sync had errors (IXL/SGY may not be installed yet)"
fi

# ---------------------------------------------------------------
# 6. Smoke test
# ---------------------------------------------------------------
log "Running smoke test..."
if school-state show > /dev/null 2>&1; then
    log "Smoke test PASSED"
    school-state show
else
    log "WARN: Smoke test failed — state file may be empty"
fi

# ---------------------------------------------------------------
# Done
# ---------------------------------------------------------------
log ""
log "Installation complete!"
log ""
log "  CLI:        $(which school-state)"
log "  Sync:       /opt/school-dashboard/school-sync.sh"
log "  State:      /var/lib/openclaw/school-state.json"
log "  Dashboard:  /var/lib/openclaw/school-dashboard.html"
log "  Cron:       6am daily (school-sync.sh)"
log ""
log "Next steps:"
log "  1. Update OpenClaw crons with prompts from /opt/school-dashboard/cron-prompts/"
log "  2. Delete the separate 'IXL Daily Report' cron (merged into afternoon update)"
log "  3. Fix evening email model: change 'Haiku' to correct model ID"
log "  4. Optional: serve dashboard via nginx:"
log "     location /school { alias /var/lib/openclaw/school-dashboard.html; }"
log ""
log "To upgrade later:"
log "  pip install --upgrade git+https://github.com/bearyjd/school-dashboard --break-system-packages -q"
log "  git -C /opt/school-dashboard pull --ff-only -q"
