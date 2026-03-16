# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Personal trip analytics pipeline for a Lexus NX 350h. Fetches trip data from the Lexus Link+ API (via `pytoyoda`), stores it in SQLite, and generates a self-contained HTML dashboard with Chart.js and Leaflet heatmaps.

## Commands

```bash
# Activate virtualenv
source .venv/bin/activate

# Fetch new trips (incremental, from last known trip date)
LEXUS_USERNAME=... LEXUS_PASSWORD=... LEXUS_VIN=... python backfill.py

# Full historical backfill (from 2024-01-01)
LEXUS_USERNAME=... LEXUS_PASSWORD=... LEXUS_VIN=... python backfill.py --full

# Regenerate dashboard from trips.db
python build_dashboard.py
```

## Architecture

**Two-script pipeline:**

1. **`backfill.py`** — Async data ingestion from Lexus Link+ API (`pytoyoda.client.MyT`, brand="L"). Fetches trips in 30-day windows, upserts into SQLite with waypoints. Supports incremental (default) and full (`--full`) modes.

2. **`build_dashboard.py`** — Reads `trips.db`, computes aggregations (monthly stats, KPIs, seasonal analysis, trip categories, score distribution, rolling fuel efficiency), and generates a single `dashboard.html` with all data embedded as JSON. Uses Tailwind CSS, Chart.js, and Leaflet with heatmap overlay.

**Database (`trips.db`):** Two tables — `trips` (keyed by `trip_start_time`) and `waypoints` (keyed by `trip_start_time` + `idx`). The `fuel_consumed_l` column stores liters despite the code variable name `fuel_ml`.

**VIN directory** — Raw CSV/JSON exports from Toyota EDA portal (gitignored data files, directory tracked).

## Key Details

- Fuel prices are hardcoded monthly PB95 averages in PLN (`FUEL_PRICES_PLN` dict in `build_dashboard.py`). Update this dict when adding new months.
- CO2 calculation uses 2.31 kg/L gasoline factor.
- Dashboard HTML is ~5 MB (embedded waypoint heatmap data) — fully self-contained, no server needed.
- Python 3.14 virtualenv in `.venv/`. Key dependency: `pytoyoda` for Lexus/Toyota API access.
