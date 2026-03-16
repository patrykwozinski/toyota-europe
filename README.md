# Trip Dashboard

The Toyota/Lexus connected car API only keeps about a year of trip history — if you don't save it, it's gone. This tool backfills your trips into a local SQLite database and generates a self-contained HTML dashboard with charts and a heatmap.

Set up a daily cron job and never lose a trip again.

Works with all vehicles on your account automatically — no VIN needed.

## What you get

A self-contained HTML dashboard per vehicle with:

- Monthly distance, fuel consumption, and cost breakdown
- Driving score analysis
- Trip heatmap on a real map
- EV vs fuel driving split (for hybrids)
- Speed analytics and highway/city split
- Seasonal and time-of-day patterns
- Service history and telemetry tracking
- Top trips table

All data stays local on your machine. Each dashboard is a single HTML file — no server needed.

## Setup

Requires **Python 3.12+**.

```bash
git clone git@github.com:patrykwozinski/toyota-europe.git
cd toyota-europe

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

You only need your Toyota/Lexus app credentials (the email and password you use to log in to the MyToyota or MyLexus app).

```bash
source .venv/bin/activate

# First run — full historical backfill for all vehicles on your account
CAR_USERNAME="your@email.com" CAR_PASSWORD="your-password" python backfill.py --full

# Subsequent runs — fetches only new trips since last run
CAR_USERNAME="your@email.com" CAR_PASSWORD="your-password" python backfill.py

# For Toyota vehicles, add CAR_BRAND=T (defaults to L for Lexus)
CAR_BRAND=T CAR_USERNAME="your@email.com" CAR_PASSWORD="your-password" python backfill.py

# For diesel vehicles
CAR_FUEL_TYPE=diesel CAR_USERNAME="your@email.com" CAR_PASSWORD="your-password" python backfill.py

# Generate dashboards (one HTML file per vehicle)
python build_dashboard.py

# Dashboard with fuel prices for a different country
python build_dashboard.py --country DE

# Polish data shown in EUR (applies exchange rate conversion)
python build_dashboard.py --country PL --currency EUR

# Open your dashboard
open dashboard_YourCarName.html
```

The script automatically discovers all vehicles on your account and processes each one. No VIN configuration needed. Fuel type (gasoline/diesel/lpg) is auto-detected from the API — use `CAR_FUEL_TYPE` to override.

### Supported countries

The `--country` flag accepts any EU country code plus GB, NO, and CH. Fuel prices are auto-scraped from GlobalPetrolPrices.com and cached locally. Use `--currency` to convert to a different display currency (exchange rates from open.er-api.com).

```
AT BE BG CH CZ DE DK EE ES FI FR GB GR HR HU IE IT LT LV NL NO PL PT RO SE SI SK
```

## Automate with cron

Run the backfill daily so you never miss a trip:

```bash
# Edit your crontab
crontab -e

# Add this line (runs daily at 8 AM)
0 8 * * * cd /path/to/toyota-europe && .venv/bin/python backfill.py
```

Set `CAR_USERNAME` and `CAR_PASSWORD` as environment variables in your shell profile, or pass them inline in the crontab entry.
