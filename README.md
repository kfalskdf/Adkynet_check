# Adkynet Check

CF Tunnel and Adkynet service monitoring scripts for Qinglong panel.

## Scripts

| Script | Description |
|--------|-------------|
| `scripts/monitor.py` | Python version (requires Selenium + Chrome/Chromium) |

## Setup

1. Copy `.env.example` to `.env` and fill in values
2. Install dependencies:

```bash
pip install requests selenium
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `CLOUDFLARE_API_TOKEN` | Cloudflare API Token (needs Zero Trust Tunnels:Read permission) |
| `CLOUDFLARE_ACCOUNT_ID` | From Cloudflare Dashboard overview |
| `CLOUDFLARE_TUNNEL_ID` | From Cloudflare Dashboard > Network > Tunnels |
| `ADKYNET_USER` | Same for manager and panel |
| `MANAGER_PASS` | Password for manager.adkynet.com |
| `PANEL_PASS` | Password for panel.adkynet.com (different) |
| `GOTIFY_URL` | Gotify server URL |
| `GOTIFY_TOKEN` | Gotify app token |

## Workflow

1. Check CF Tunnel health via Cloudflare API → healthy = exit
2. If unhealthy, login manager.adkynet.com → check expiry date → 3 days or less = alert
3. If not expiring soon, login panel.adkynet.com → check server status → send alert