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

# For diesel vehicles, set CAR_FUEL_TYPE=diesel during backfill
CAR_FUEL_TYPE=diesel CAR_USERNAME=... CAR_PASSWORD=... python backfill.py

# Regenerate dashboards from trips.db (one per vehicle)
python build_dashboard.py

# Dashboard with different country fuel prices
python build_dashboard.py --country DE

# Dashboard with currency conversion (e.g. PL prices shown in EUR)
python build_dashboard.py --country PL --currency EUR
```

## Architecture

**Two-script pipeline:**

1. **`backfill.py`** — Async data ingestion from Lexus/Toyota API (`pytoyoda.client.MyT`). Supports both Lexus (`CAR_BRAND=L`, default) and Toyota (`CAR_BRAND=T`). Automatically processes ALL vehicles on the account. Fetches trips in 30-day windows, upserts into SQLite with waypoints. Supports incremental (default) and full (`--full`) modes. `CAR_VIN` is no longer required. Set `CAR_FUEL_TYPE` to `diesel`/`lpg` for non-petrol vehicles (default: `gasoline`).

2. **`build_dashboard.py`** — Reads `trips.db`, generates a separate `dashboard_{alias}.html` for each vehicle. Computes aggregations (monthly stats, KPIs, seasonal analysis, trip categories, score distribution, rolling fuel efficiency) per vehicle with all data embedded as JSON. Uses Tailwind CSS, Chart.js, and Leaflet with heatmap overlay. Supports `--country` (fuel price source, EU countries) and `--currency` (display currency with exchange rate conversion).

3. **`fuel_config.py`** — Country/currency registry (EU + UK), fuel price scraping from GlobalPetrolPrices.com, exchange rate fetching from open.er-api.com. Caches prices in `fuel_prices.json` and DB `fuel_prices` table for offline/cron reliability.

**Database (`trips.db`):** Tables — `trips` (keyed by `trip_start_time`), `waypoints` (keyed by `trip_start_time` + `idx`), `vehicles` (keyed by `vin`, has `fuel_type` column), `fuel_prices` (keyed by `country` + `month` + `fuel_type`). The `fuel_consumed_l` column stores liters despite the code variable name `fuel_ml`.

**VIN directory** — Raw CSV/JSON exports from Toyota EDA portal (gitignored data files, directory tracked).

## Key Details

- Fuel prices are stored in the `fuel_prices` DB table and cached in `fuel_prices.json`. Legacy PL gasoline prices are seeded from `LEGACY_PL_PRICES` in `fuel_config.py`. New months are auto-scraped from GlobalPetrolPrices.com on dashboard build.
- CO2 calculation uses 2.31 kg/L gasoline factor.
- Dashboard HTML is ~5 MB (embedded waypoint heatmap data) — fully self-contained, no server needed.
- Python 3.14 virtualenv in `.venv/`. Key dependency: `pytoyoda` for Lexus/Toyota API access.
