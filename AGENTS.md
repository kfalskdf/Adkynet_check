# AGENTS.md

## Overview
CF Tunnel and Adkynet service monitoring scripts for Qinglong panel.

## Scripts
- `scripts/monitor.py` - Python version (requires Selenium + Chrome)
- `scripts/monitor.sh` - Bash version (uses curl)

## Required Environment Variables
```
CLOUDFLARE_API_TOKEN   # Needs "Zero Trust Tunnels:Read" permission
CLOUDFLARE_ACCOUNT_ID  # From Cloudflare Dashboard overview
CLOUDFLARE_TUNNEL_ID   # From Cloudflare Dashboard > Network > Tunnels
ADKYNET_USER           # Same for manager and panel
MANAGER_PASS           # Password for manager.adkynet.com
PANEL_PASS             # Password for panel.adkynet.com (different from manager)
GOTIFY_URL             # e.g., https://gotify.example.com
GOTIFY_TOKEN           # Gotify app token
```

## Dependencies (Python version)
```bash
pip install requests selenium
# Also requires chromedriver
```

## Workflow
1. Check CF Tunnel health via Cloudflare API → healthy = exit
2. If unhealthy, login manager.adkynet.com → check expiry date → 3 days or less = alert + exit
3. If not expiring soon, login panel.adkynet.com → check server status → send alert