# property-alert

Automated UK property search. Monitors Rightmove, Zoopla, OnTheMarket, and OpenRent for new listings matching your criteria, then emails you a digest twice a day.

**100% free** — no paid APIs, no proxies, no subscriptions.

## How it works

```
Scheduler (every 6h)
  → Scrape all sources (Rightmove, Zoopla, OnTheMarket, OpenRent)
  → Filter against your search criteria
  → Enrich new matches (crime stats, EPC, commute time, schools)
  → Queue for digest email (sent at 09:00 and 18:00)
```

Only properties you have never been notified about trigger an alert. No spam.

## Quick start

### 1. Install

```bash
cd property-alert
pip install -e .
playwright install chromium      # needed for OnTheMarket
```

### 2. Set up

```bash
property-alert init
```

The interactive wizard creates `~/.config/property-alert/config.yaml` and walks you through:
- Gmail credentials (you need a [Gmail App Password](https://myaccount.google.com/apppasswords), not your regular password)
- Your search criteria (location, price, bedrooms, etc.)
- Optional: Google Maps API key for commute times
- Optional: EPC API key for energy ratings

### 3. Test

```bash
property-alert search            # run one cycle right now
property-alert recent            # see what was found
```

### 4. Run continuously

```bash
property-alert run               # starts the scheduler (keep this terminal open)
```

For a permanent background service, see [Running as a service](#running-as-a-service) below.

---

## CLI reference

| Command | What it does |
|---|---|
| `property-alert init` | First-time setup wizard |
| `property-alert run` | Start the scheduler daemon |
| `property-alert search` | Run one search cycle immediately |
| `property-alert search --source rightmove` | Search only one source |
| `property-alert search --no-notify` | Search without sending email |
| `property-alert search --dry-run` | Search without saving or notifying |
| `property-alert recent [N]` | Show last N matched properties (default 20) |
| `property-alert status` | Show DB stats and active searches |
| `property-alert config show` | Print config (password redacted) |
| `property-alert config edit` | Open config in `$EDITOR` |

---

## Config reference (`~/.config/property-alert/config.yaml`)

See [`config/example_config.yaml`](config/example_config.yaml) for a full annotated example.

Key fields:

```yaml
schedule_hours: 6             # how often to scrape (hours)
notification:
  digest_times: ["09:00", "18:00"]   # when to send emails
  email:
    from: you@gmail.com
    to: you@gmail.com
    password: xxxx-xxxx-xxxx-xxxx    # Gmail App Password

searches:
  - id: my-search
    label: "2-bed flat London"
    type: rent                       # rent | sale | both
    sources: [rightmove, zoopla, onthemarket, openrent]
    location: "London"
    radius_miles: 2
    min_price: 1500
    max_price: 2500
    min_bedrooms: 2
    max_bedrooms: 3
    property_types: [flat]           # leave empty for any
    keywords_require: []
    keywords_exclude: [studio]
    enrichment:
      commute_to: "Bank Station, London"
      include_crime: true
      include_epc: true
      include_schools: true
```

---

## Enrichment features

All enrichment uses **free public APIs**:

| Feature | Source | Requires |
|---|---|---|
| Crime stats | [data.police.uk](https://data.police.uk) | Nothing |
| School Ofsted rating | [get-information-schools.service.gov.uk](https://get-information-schools.service.gov.uk) | Nothing |
| Sold price average | [Land Registry SPARQL](https://landregistry.data.gov.uk) | Nothing |
| EPC energy rating | [epc.opendatacommunities.org](https://epc.opendatacommunities.org) | Free account |
| Commute time | [Google Maps Distance Matrix](https://developers.google.com/maps/documentation/distance-matrix) | Free API key ($200/mo credit) |

---

## Running as a service (Linux)

Create `/etc/systemd/system/property-alert.service`:

```ini
[Unit]
Description=Property Alert Scheduler
After=network.target

[Service]
User=YOUR_USERNAME
ExecStart=/usr/bin/python3 -m property_alert.cli run
WorkingDirectory=/home/YOUR_USERNAME
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable --now property-alert
sudo journalctl -fu property-alert    # view logs
```

---

## Scraping notes

- **Rightmove**: uses the internal `/api/_search` JSON endpoint — no browser needed, fast and reliable
- **Zoopla**: scrapes the `__NEXT_DATA__` JSON blob embedded in pages
- **OnTheMarket**: uses Playwright (headless browser) — lists properties up to 24h before Rightmove/Zoopla
- **OpenRent**: straightforward HTML scraping — direct landlord listings only (rent only)

Both Rightmove and Zoopla's Terms of Service prohibit automated access. This tool is for personal, private use only. Run it at a respectful interval (default: every 6 hours) to avoid overloading their servers.

---

## Environment variables

Alternatively to storing secrets in the YAML config, use a `.env` file (see `.env.example`):

```
EMAIL_FROM=you@gmail.com
EMAIL_TO=you@gmail.com
EMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
GOOGLE_MAPS_API_KEY=
EPC_API_KEY=
```
