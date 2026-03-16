# Trip Dashboard

Trip analytics dashboard for **Lexus** and **Toyota** connected vehicles.
Fetches trip data from the manufacturer API, stores it locally, and generates a self-contained HTML dashboard with charts and a heatmap.

## Setup

Requires **Python 3.12+**.

```bash
# 1. Clone / unzip the project
cd trips

# 2. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Set your credentials
export CAR_USERNAME="your-toyota-or-lexus-email"
export CAR_PASSWORD="your-password"
export CAR_VIN="your-vehicle-vin"

# For Toyota vehicles (Yaris, Corolla, RAV4, etc.):
export CAR_BRAND="T"

# For Lexus vehicles (default, can be omitted):
export CAR_BRAND="L"
```

## Finding your VIN

Your VIN (Vehicle Identification Number) is required — it identifies your specific car.
You can find it in your **Toyota/Lexus app** under vehicle details, or on the sticker inside the driver's door frame.

## Usage

```bash
# Activate the virtualenv
source .venv/bin/activate

# First run — full historical backfill
CAR_USERNAME=... CAR_PASSWORD=... CAR_VIN=... CAR_BRAND=T python backfill.py --full

# Subsequent runs — fetches only new trips
CAR_USERNAME=... CAR_PASSWORD=... CAR_VIN=... CAR_BRAND=T python backfill.py

# Generate the dashboard
python build_dashboard.py

# Open dashboard.html in your browser — that's it!
open dashboard.html
```

## What you get

- Monthly distance, fuel consumption, and cost breakdown
- Driving score analysis
- Trip heatmap on a real map
- EV vs fuel driving split (for hybrids)
- Seasonal and time-of-day patterns
- And more

All data stays local on your machine. The dashboard is a single HTML file — no server needed.
