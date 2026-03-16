# Trip Dashboard

Trip analytics dashboard for **Lexus** and **Toyota** connected vehicles.
Fetches trip data from the manufacturer API, stores it locally, and generates a self-contained HTML dashboard with charts and a heatmap.

Works with all vehicles on your account automatically — no VIN needed.

## Setup

Requires **Python 3.12+**.

```bash
# 1. Clone the project
git clone <repo-url>
cd trips

# 2. Create a virtual environment and install dependencies
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

# Generate dashboards (one HTML file per vehicle)
python build_dashboard.py

# Open your dashboard
open dashboard_YourCarName.html
```

The script automatically discovers all vehicles on your account and processes each one. No VIN configuration needed.

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
