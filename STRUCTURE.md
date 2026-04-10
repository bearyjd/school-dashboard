# School Dashboard — Server Layout

## Host: root@192.168.1.14 (Ubuntu 24.04)

### Application files

```
/opt/school/
├── dashboard/                        # school-dashboard Python package + sync script
│   ├── school_dashboard/
│   │   ├── cli.py                    # school-state CLI entrypoint
│   │   ├── state.py                  # state manager (reads /opt/school/state/)
│   │   ├── html.py                   # dashboard HTML renderer
│   │   ├── email.py                  # Gmail email sync via gog
│   │   └── templates/
│   ├── school-sync.sh                # main cron script (scrape → merge → html)
│   └── pyproject.toml
│
├── ixl/                              # IXL scraper
│   ├── ixl_cli/
│   ├── cron/
│   │   └── ixl-cron.sh              # reads /opt/school/config/ixl-accounts.env
│   └── pyproject.toml
│
├── state/                            # runtime data (was /var/lib/openclaw/)
│   ├── school-state.json             # merged state: IXL + Schoology + action items
│   ├── school-dashboard.html         # generated dashboard
│   ├── email-digest.json             # processed email digest
│   └── email-attachments/            # downloaded PDF/attachments
│
├── config/                           # configuration
│   ├── config.json                   # children config + name aliases
│   ├── env                           # secrets + env vars (chmod 600)
│   └── ixl-accounts.env             # IXL per-child credentials (chmod 600)
│
└── gog/                              # gog (gogcli) OAuth keyring
    └── keyring                       # encrypted token store for parent@example.com
```

### Binaries

```
/usr/local/bin/
├── gog          # Google Workspace CLI (gogcli.sh) — Gmail, Calendar, Drive
├── sgy          # Schoology scraper CLI
├── school-state # school-dashboard state manager CLI
└── signal-cli   # Signal messenger CLI (v0.13.24) — NOT YET CONFIGURED
```

### Cron (root crontab)

```
0 6    * * *   /opt/school/dashboard/school-sync.sh 2>>/tmp/school-sync.log
30 14  * * 1-5 /opt/school/dashboard/school-sync.sh 2>>/tmp/school-sync.log
```

### Secrets (local copies in ./secrets/ — DO NOT COMMIT)

```
secrets/
├── client_secret.json    # Google OAuth client credentials (used by gog auth)
├── gog/
│   └── keyring           # gog encrypted token store for parent@example.com
├── ixl-accounts.env      # IXL logins: per-child credentials
└── env                   # consolidated env: GOG_KEYRING_PASSWORD, SCHOOL_* paths, etc.
```

### Still TODO

- [ ] signal-cli: register a bot number and configure recipients
  - `+12026569253` was attempted but not registered
  - Need a VoIP number (JMP.chat recommended) or link an existing device
- [ ] Add `GOG_KEYRING_PASSWORD` to `/opt/school/config/env`
- [ ] Add Signal send step to `school-sync.sh` (morning digest)
- [ ] Uninstall openclaw binary (`/usr/bin/openclaw`) once confirmed stable
