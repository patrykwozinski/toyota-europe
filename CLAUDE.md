# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Trip analytics pipeline for Lexus/Toyota vehicles. Fetches trip data from the connected car API (via `pytoyoda`), stores it in SQLite, and generates a self-contained HTML dashboard with Chart.js and Leaflet heatmaps.

## Commands

```bash
# Activate virtualenv
source .venv/bin/activate

# Fetch new trips for ALL vehicles on the account (incremental)
CAR_USERNAME=... CAR_PASSWORD=... python backfill.py

# Full historical backfill (from 2024-01-01)
CAR_USERNAME=... CAR_PASSWORD=... python backfill.py --full

# For Toyota vehicles, set CAR_BRAND=T (defaults to L for Lexus)
CAR_BRAND=T CAR_USERNAME=... CAR_PASSWORD=... python backfill.py

# Regenerate dashboards from trips.db (one per vehicle)
python build_dashboard.py
```

## Architecture

**Two-script pipeline:**

1. **`backfill.py`** — Async data ingestion from Lexus/Toyota API (`pytoyoda.client.MyT`). Supports both Lexus (`CAR_BRAND=L`, default) and Toyota (`CAR_BRAND=T`). Automatically processes ALL vehicles on the account. Fetches trips in 30-day windows, upserts into SQLite with waypoints. Supports incremental (default) and full (`--full`) modes. `CAR_VIN` is no longer required.

2. **`build_dashboard.py`** — Reads `trips.db`, generates a separate `dashboard_{alias}.html` for each vehicle. Computes aggregations (monthly stats, KPIs, seasonal analysis, trip categories, score distribution, rolling fuel efficiency) per vehicle with all data embedded as JSON. Uses Tailwind CSS, Chart.js, and Leaflet with heatmap overlay.

**Database (`trips.db`):** Tables — `trips` (keyed by `trip_start_time`), `waypoints` (keyed by `trip_start_time` + `idx`), `vehicles` (keyed by `vin`). The `fuel_consumed_l` column stores liters despite the code variable name `fuel_ml`.

**VIN directory** — Raw CSV/JSON exports from Toyota EDA portal (gitignored data files, directory tracked).

## Key Details

- Fuel prices are hardcoded monthly PB95 averages in PLN (`FUEL_PRICES_PLN` dict in `build_dashboard.py`). Update this dict when adding new months.
- CO2 calculation uses 2.31 kg/L gasoline factor.
- Dashboard HTML is ~5 MB (embedded waypoint heatmap data) — fully self-contained, no server needed.
- Python 3.14 virtualenv in `.venv/`. Key dependency: `pytoyoda` for Lexus/Toyota API access.
