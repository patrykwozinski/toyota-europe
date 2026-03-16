"""Build a self-contained HTML dashboard from trips.db."""

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "trips.db"
OUTPUT = Path(__file__).parent / "dashboard.html"

# Monthly average PB95 prices in Poland (PLN/L)
# Source: e-petrol.pl, bankier.pl, fuelo.net
FUEL_PRICES_PLN = {
    "2025-03": 5.96, "2025-04": 5.92, "2025-05": 5.74,
    "2025-06": 6.01, "2025-07": 5.90, "2025-08": 5.80,
    "2025-09": 5.82, "2025-10": 5.84, "2025-11": 5.80,
    "2025-12": 5.73, "2026-01": 5.59, "2026-02": 5.71,
    "2026-03": 6.19,
}
FUEL_PRICE_DEFAULT = 5.90  # fallback for months not in the table

# CO2 emission factor for gasoline: 2.31 kg CO2 per liter
CO2_KG_PER_LITER = 2.31


def fuel_price_for(month: str) -> float:
    return FUEL_PRICES_PLN.get(month, FUEL_PRICE_DEFAULT)


def load_trips(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trips ORDER BY trip_start_time"
    ).fetchall()
    result = []
    for r in rows:
        start = datetime.fromisoformat(r["trip_start_time"]) if r["trip_start_time"] else None
        if not start:
            continue
        result.append({
            "start": start,
            "end": datetime.fromisoformat(r["trip_end_time"]) if r["trip_end_time"] else None,
            "duration_sec": r["duration_sec"] or 0,
            "distance_km": r["distance_km"] or 0,
            "ev_distance_km": r["ev_distance_km"] or 0,
            "fuel_ml": r["fuel_consumed_l"] or 0,
            "avg_fuel": r["avg_fuel_l100km"] or 0,
            "start_lat": r["start_lat"],
            "start_lng": r["start_lng"],
            "end_lat": r["end_lat"],
            "end_lng": r["end_lng"],
            "score": r["score"],
            "score_accel": r["score_accel"],
            "score_brake": r["score_braking"],
        })
    conn.row_factory = None
    return result


def load_heatmap_points(conn: sqlite3.Connection) -> list[list]:
    rows = conn.execute(
        "SELECT ROUND(lat, 4) AS rlat, ROUND(lng, 4) AS rlng, COUNT(*) AS cnt "
        "FROM waypoints GROUP BY rlat, rlng"
    ).fetchall()
    return [[r[0], r[1], math.log1p(r[2])] for r in rows]


def compute_monthly(trips: list[dict]) -> dict:
    months: dict[str, dict] = defaultdict(lambda: {
        "trips": 0, "distance": 0, "ev_distance": 0, "fuel": 0, "duration": 0,
        "scores": [],
    })
    for t in trips:
        key = t["start"].strftime("%Y-%m")
        m = months[key]
        m["trips"] += 1
        m["distance"] += t["distance_km"]
        m["ev_distance"] += t["ev_distance_km"]
        m["fuel"] += t["fuel_ml"]
        m["duration"] += t["duration_sec"]
        if t["score"] is not None:
            m["scores"].append(t["score"])

    labels = sorted(months.keys())
    return {
        "labels": labels,
        "trips": [months[k]["trips"] for k in labels],
        "distance": [round(months[k]["distance"], 1) for k in labels],
        "ev_distance": [round(months[k]["ev_distance"], 1) for k in labels],
        "fuel": [round(months[k]["fuel"], 1) for k in labels],
        "avg_fuel": [
            round(months[k]["fuel"] / months[k]["distance"] * 100, 2)
            if months[k]["distance"] > 0 else 0
            for k in labels
        ],
        "avg_score": [
            round(sum(months[k]["scores"]) / len(months[k]["scores"]), 1)
            if months[k]["scores"] else 0
            for k in labels
        ],
        "fuel_cost_pln": [
            round(months[k]["fuel"] * fuel_price_for(k), 2)
            for k in labels
        ],
        "fuel_price": [fuel_price_for(k) for k in labels],
    }


def compute_weekday_hour(trips: list[dict]) -> dict:
    weekday_counts = [0] * 7
    weekday_dist = [0.0] * 7
    hour_counts = [0] * 24
    hour_dist = [0.0] * 24
    for t in trips:
        wd = t["start"].weekday()
        h = t["start"].hour
        weekday_counts[wd] += 1
        weekday_dist[wd] += t["distance_km"]
        hour_counts[h] += 1
        hour_dist[h] += t["distance_km"]
    return {
        "weekday_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "weekday_trips": weekday_counts,
        "weekday_distance": [round(d, 1) for d in weekday_dist],
        "hour_labels": [f"{h:02d}" for h in range(24)],
        "hour_trips": hour_counts,
        "hour_distance": [round(d, 1) for d in hour_dist],
    }


def compute_score_distribution(trips: list[dict]) -> dict:
    buckets = defaultdict(int)
    for t in trips:
        if t["score"] is not None:
            bucket = (t["score"] // 5) * 5
            buckets[bucket] += 1
    labels = list(range(0, 101, 5))
    return {
        "labels": [f"{b}-{b+4}" for b in labels],
        "counts": [buckets.get(b, 0) for b in labels],
    }


def compute_trip_categories(trips: list[dict]) -> dict:
    cats = {"Micro (<2 km)": 0, "Short (2-10 km)": 0, "Medium (10-30 km)": 0,
            "Long (30-100 km)": 0, "Road trip (>100 km)": 0}
    for t in trips:
        d = t["distance_km"]
        if d < 2:
            cats["Micro (<2 km)"] += 1
        elif d < 10:
            cats["Short (2-10 km)"] += 1
        elif d < 30:
            cats["Medium (10-30 km)"] += 1
        elif d < 100:
            cats["Long (30-100 km)"] += 1
        else:
            cats["Road trip (>100 km)"] += 1
    return {"labels": list(cats.keys()), "counts": list(cats.values())}


def compute_seasonal(trips: list[dict]) -> dict:
    seasons = {"Winter": {"ev": 0, "total": 0, "fuel": 0, "dist": 0},
               "Spring": {"ev": 0, "total": 0, "fuel": 0, "dist": 0},
               "Summer": {"ev": 0, "total": 0, "fuel": 0, "dist": 0},
               "Autumn": {"ev": 0, "total": 0, "fuel": 0, "dist": 0}}
    for t in trips:
        m = t["start"].month
        if m in (12, 1, 2):
            s = "Winter"
        elif m in (3, 4, 5):
            s = "Spring"
        elif m in (6, 7, 8):
            s = "Summer"
        else:
            s = "Autumn"
        seasons[s]["ev"] += t["ev_distance_km"]
        seasons[s]["total"] += t["distance_km"]
        seasons[s]["fuel"] += t["fuel_ml"]
        seasons[s]["dist"] += t["distance_km"]
    labels = ["Winter", "Spring", "Summer", "Autumn"]
    return {
        "labels": labels,
        "ev_ratio": [
            round(seasons[s]["ev"] / seasons[s]["total"] * 100, 1)
            if seasons[s]["total"] > 0 else 0
            for s in labels
        ],
        "avg_fuel": [
            round(seasons[s]["fuel"] / seasons[s]["dist"] * 100, 2)
            if seasons[s]["dist"] > 0 else 0
            for s in labels
        ],
    }


def top_trips(trips: list[dict], n: int = 15) -> list[dict]:
    longest = sorted(trips, key=lambda t: t["distance_km"], reverse=True)[:n]
    result = []
    for t in longest:
        month = t["start"].strftime("%Y-%m")
        cost = t["fuel_ml"] * fuel_price_for(month)
        result.append({
            "date": t["start"].strftime("%Y-%m-%d %H:%M"),
            "distance": round(t["distance_km"], 1),
            "duration_min": round(t["duration_sec"] / 60, 1),
            "fuel": round(t["fuel_ml"], 2),
            "cost": round(cost, 2),
            "ev_pct": round(t["ev_distance_km"] / t["distance_km"] * 100, 1) if t["distance_km"] > 0 else 0,
            "score": t["score"],
        })
    return result


def compute_kpis(trips: list[dict]) -> dict:
    total_dist = sum(t["distance_km"] for t in trips)
    total_ev = sum(t["ev_distance_km"] for t in trips)
    total_fuel = sum(t["fuel_ml"] for t in trips)
    total_dur = sum(t["duration_sec"] for t in trips)
    scores = [t["score"] for t in trips if t["score"] is not None]
    first = trips[0]["start"].strftime("%b %d, %Y") if trips else "N/A"
    last = trips[-1]["start"].strftime("%b %d, %Y") if trips else "N/A"

    # Fuel cost
    total_cost = 0.0
    for t in trips:
        month = t["start"].strftime("%Y-%m")
        total_cost += t["fuel_ml"] * fuel_price_for(month)

    # CO2
    co2_emitted = total_fuel * CO2_KG_PER_LITER
    # How much CO2 would have been emitted if EV km were driven on ICE instead
    avg_l100 = total_fuel / total_dist * 100 if total_dist > 0 else 0
    co2_saved = (total_ev * avg_l100 / 100) * CO2_KG_PER_LITER if total_dist > 0 else 0

    return {
        "total_trips": len(trips),
        "total_distance_km": round(total_dist, 1),
        "total_ev_km": round(total_ev, 1),
        "ev_ratio_pct": round(total_ev / total_dist * 100, 1) if total_dist > 0 else 0,
        "total_fuel_l": round(total_fuel, 1),
        "avg_fuel_l100km": round(avg_l100, 2),
        "total_hours": round(total_dur / 3600, 1),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "avg_trip_km": round(total_dist / len(trips), 1) if trips else 0,
        "first_trip": first,
        "last_trip": last,
        "total_cost_pln": round(total_cost, 0),
        "cost_per_km": round(total_cost / total_dist, 2) if total_dist > 0 else 0,
        "co2_emitted_kg": round(co2_emitted, 1),
        "co2_saved_kg": round(co2_saved, 1),
    }


def build_html(kpis, monthly, weekday_hour, score_dist, heatmap_pts, longest_trips,
               trip_cats, seasonal, trips):
    lats = [t["start_lat"] for t in trips if t["start_lat"] is not None]
    lngs = [t["start_lng"] for t in trips if t["start_lng"] is not None]
    center_lat = sorted(lats)[len(lats) // 2] if lats else 52.1
    center_lng = sorted(lngs)[len(lngs) // 2] if lngs else 20.9

    rolling_fuel = []
    window = 20
    for i in range(len(trips)):
        chunk = trips[max(0, i - window + 1):i + 1]
        dist = sum(t["distance_km"] for t in chunk)
        fuel = sum(t["fuel_ml"] for t in chunk)
        if dist > 0:
            rolling_fuel.append({
                "x": trips[i]["start"].strftime("%Y-%m-%d"),
                "y": round(fuel / dist * 100, 2),
            })

    data = {
        "kpis": kpis,
        "monthly": monthly,
        "weekdayHour": weekday_hour,
        "scoreDist": score_dist,
        "heatmap": heatmap_pts,
        "center": [center_lat, center_lng],
        "longestTrips": longest_trips,
        "rollingFuel": rolling_fuel,
        "tripCats": trip_cats,
        "seasonal": seasonal,
    }

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lexus NX350h Trip Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.heat@0.2/dist/leaflet-heat.js"></script>
<script>
tailwind.config = {{
  darkMode: 'class',
  theme: {{
    extend: {{
      colors: {{
        lexus: {{ 50:'#f7f6f4', 100:'#eceae4', 200:'#d8d3c8', 300:'#c0b8a5', 400:'#a69b82', 500:'#917f65', 600:'#84725a', 700:'#6c5d4b', 800:'#5a4e41', 900:'#4a4137', 950:'#28221d' }},
      }}
    }}
  }}
}}
</script>
<style>
  body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif; }}
  .card {{ @apply bg-gray-800/50 backdrop-blur rounded-2xl border border-gray-700/50 p-6; }}
  #heatmap {{ height: 500px; border-radius: 1rem; z-index: 1; }}
  .kpi-value {{ @apply text-3xl font-bold text-white; }}
  .kpi-label {{ @apply text-sm text-gray-400 mt-1; }}
  .section-title {{ @apply text-xl font-semibold text-white mb-6 pb-2 border-b border-gray-800; }}
</style>
</head>
<body class="dark bg-gray-950 text-gray-200 min-h-screen">
<div class="max-w-7xl mx-auto px-4 py-8">

  <!-- Header -->
  <div class="flex items-center justify-between mb-8">
    <div>
      <h1 class="text-3xl font-bold text-white tracking-tight">Lexus NX 350h</h1>
      <p class="text-gray-400 mt-1">Omotenashi 2024 &middot; Trip Analytics Dashboard</p>
    </div>
    <div class="text-right text-sm text-gray-500">
      <div>{kpis['first_trip']} &mdash; {kpis['last_trip']}</div>
      <div>{kpis['total_trips']} trips recorded</div>
    </div>
  </div>

  <!-- KPI Cards -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
    <div class="card">
      <div class="kpi-value">{kpis['total_trips']}</div>
      <div class="kpi-label">Total Trips</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_distance_km']:,.0f}<span class="text-lg text-gray-400"> km</span></div>
      <div class="kpi-label">Total Distance</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['avg_fuel_l100km']}<span class="text-lg text-gray-400"> L/100</span></div>
      <div class="kpi-label">Avg Fuel Consumption</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['ev_ratio_pct']}<span class="text-lg text-gray-400">%</span></div>
      <div class="kpi-label">EV Distance Ratio</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['avg_score']}</div>
      <div class="kpi-label">Avg Driving Score</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_hours']:,.0f}<span class="text-lg text-gray-400"> h</span></div>
      <div class="kpi-label">Time Driving</div>
    </div>
  </div>

  <!-- Cost & Environment KPIs -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
    <div class="card">
      <div class="kpi-value">{kpis['total_cost_pln']:,.0f}<span class="text-lg text-gray-400"> PLN</span></div>
      <div class="kpi-label">Total Fuel Cost</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['cost_per_km']}<span class="text-lg text-gray-400"> PLN/km</span></div>
      <div class="kpi-label">Cost per km</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_fuel_l']:,.0f}<span class="text-lg text-gray-400"> L</span></div>
      <div class="kpi-label">Total Fuel Used</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_ev_km']:,.0f}<span class="text-lg text-gray-400"> km</span></div>
      <div class="kpi-label">EV Distance</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['co2_emitted_kg']:,.0f}<span class="text-lg text-gray-400"> kg</span></div>
      <div class="kpi-label">CO2 Emitted</div>
    </div>
    <div class="card">
      <div class="kpi-value" style="color:#22c55e">{kpis['co2_saved_kg']:,.0f}<span class="text-lg text-gray-400"> kg</span></div>
      <div class="kpi-label">CO2 Saved by EV</div>
    </div>
  </div>

  <!-- Heatmap -->
  <div class="card mb-8 !p-0 overflow-hidden">
    <div class="p-6 pb-2">
      <h2 class="text-xl font-semibold text-white">Route Heatmap</h2>
      <p class="text-sm text-gray-400">All {kpis['total_trips']} trips overlaid &middot; brighter = more frequent &middot; <span id="heatmapPts"></span> waypoints</p>
    </div>
    <div id="heatmap"></div>
  </div>

  <!-- Monthly Charts Row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">Monthly Distance</h3>
      <canvas id="monthlyDistance"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">Monthly Fuel Cost (PLN)</h3>
      <canvas id="monthlyCost"></canvas>
    </div>
  </div>

  <!-- Fuel + EV Row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">Monthly Fuel Consumption (L/100km)</h3>
      <canvas id="monthlyFuel"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">EV vs ICE Distance by Month</h3>
      <canvas id="evIce"></canvas>
    </div>
  </div>

  <!-- Trip Categories + Seasonal -->
  <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">Trip Categories</h3>
      <canvas id="tripCats"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">EV Ratio by Season</h3>
      <canvas id="seasonalEv"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">Fuel Efficiency by Season (L/100km)</h3>
      <canvas id="seasonalFuel"></canvas>
    </div>
  </div>

  <!-- Score + Time Patterns -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">Monthly Driving Score</h3>
      <canvas id="monthlyScore"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">Driving Score Distribution</h3>
      <canvas id="scoreDist"></canvas>
    </div>
  </div>

  <!-- Time Patterns Row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">Trips by Day of Week</h3>
      <canvas id="weekday"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-white mb-4">Trips by Hour of Day</h3>
      <canvas id="hourly"></canvas>
    </div>
  </div>

  <!-- Fuel Efficiency Trend -->
  <div class="card mb-8">
    <h3 class="text-lg font-semibold text-white mb-4">Fuel Efficiency Trend (20-trip rolling avg, L/100km)</h3>
    <canvas id="fuelTrend"></canvas>
  </div>

  <!-- Top Trips Table -->
  <div class="card mb-8 overflow-x-auto">
    <h3 class="text-lg font-semibold text-white mb-4">Longest Trips</h3>
    <table class="w-full text-sm">
      <thead>
        <tr class="text-gray-400 border-b border-gray-700">
          <th class="text-left py-2 px-3">Date</th>
          <th class="text-right py-2 px-3">Distance (km)</th>
          <th class="text-right py-2 px-3">Duration (min)</th>
          <th class="text-right py-2 px-3">Fuel (L)</th>
          <th class="text-right py-2 px-3">Cost (PLN)</th>
          <th class="text-right py-2 px-3">EV %</th>
          <th class="text-right py-2 px-3">Score</th>
        </tr>
      </thead>
      <tbody id="topTripsBody"></tbody>
    </table>
  </div>

  <footer class="text-center text-xs text-gray-600 py-8">
    Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} &middot; Lexus NX 350h Omotenashi 2024 Trip Dashboard
    &middot; Fuel prices: PB95 monthly avg (e-petrol.pl)
  </footer>
</div>

<script>
const D = {json.dumps(data, separators=(',', ':'))};

// --- Theme ---
const gridColor = 'rgba(255,255,255,0.06)';
const tickColor = '#9ca3af';
Chart.defaults.color = tickColor;
Chart.defaults.borderColor = gridColor;
Chart.defaults.plugins.legend.labels.boxWidth = 12;

// --- Heatmap ---
const map = L.map('heatmap').setView(D.center, 11);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution: '&copy; OSM &amp; Carto',
  subdomains: 'abcd', maxZoom: 19
}}).addTo(map);
L.heatLayer(D.heatmap, {{
  radius: 12, blur: 18, maxZoom: 17, minOpacity: 0.35,
  gradient: {{0.0:'#0d1b2a', 0.15:'#1b3a5c', 0.3:'#1976d2', 0.5:'#26c6da', 0.7:'#ffa726', 0.85:'#ef5350', 1:'#ffe66d'}}
}}).addTo(map);
document.getElementById('heatmapPts').textContent = D.heatmap.reduce((s,p) => s+p[2], 0).toLocaleString();

// --- Monthly Distance ---
new Chart(document.getElementById('monthlyDistance'), {{
  type: 'bar',
  data: {{
    labels: D.monthly.labels,
    datasets: [{{
      label: 'Distance (km)',
      data: D.monthly.distance,
      backgroundColor: 'rgba(14,165,233,0.7)',
      borderRadius: 6,
    }}, {{
      label: 'Trips',
      data: D.monthly.trips,
      type: 'line',
      borderColor: '#f59e0b',
      backgroundColor: 'transparent',
      yAxisID: 'y1',
      tension: 0.3,
      pointRadius: 3,
    }}]
  }},
  options: {{
    responsive: true,
    interaction: {{ mode: 'index', intersect: false }},
    scales: {{
      y: {{ title: {{ display: true, text: 'km' }}, grid: {{ color: gridColor }} }},
      y1: {{ position: 'right', title: {{ display: true, text: 'Trips' }}, grid: {{ display: false }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Monthly Cost ---
new Chart(document.getElementById('monthlyCost'), {{
  type: 'bar',
  data: {{
    labels: D.monthly.labels,
    datasets: [{{
      label: 'Fuel Cost (PLN)',
      data: D.monthly.fuel_cost_pln,
      backgroundColor: 'rgba(234,179,8,0.7)',
      borderRadius: 6,
    }}, {{
      label: 'PB95 Price (PLN/L)',
      data: D.monthly.fuel_price,
      type: 'line',
      borderColor: '#ef4444',
      backgroundColor: 'transparent',
      yAxisID: 'y1',
      tension: 0.3,
      pointRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    interaction: {{ mode: 'index', intersect: false }},
    scales: {{
      y: {{ title: {{ display: true, text: 'PLN' }}, grid: {{ color: gridColor }} }},
      y1: {{ position: 'right', title: {{ display: true, text: 'PLN/L' }}, grid: {{ display: false }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Monthly Fuel ---
new Chart(document.getElementById('monthlyFuel'), {{
  type: 'line',
  data: {{
    labels: D.monthly.labels,
    datasets: [{{
      label: 'L/100km',
      data: D.monthly.avg_fuel,
      borderColor: '#ef4444',
      backgroundColor: 'rgba(239,68,68,0.1)',
      fill: true,
      tension: 0.3,
      pointRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      y: {{ title: {{ display: true, text: 'L/100km' }}, grid: {{ color: gridColor }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- EV vs ICE ---
new Chart(document.getElementById('evIce'), {{
  type: 'bar',
  data: {{
    labels: D.monthly.labels,
    datasets: [{{
      label: 'EV (km)',
      data: D.monthly.ev_distance,
      backgroundColor: 'rgba(34,197,94,0.7)',
      borderRadius: 6,
    }}, {{
      label: 'ICE (km)',
      data: D.monthly.distance.map((d,i) => Math.max(0, +(d - D.monthly.ev_distance[i]).toFixed(1))),
      backgroundColor: 'rgba(239,68,68,0.5)',
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, title: {{ display: true, text: 'km' }}, grid: {{ color: gridColor }} }}
    }}
  }}
}});

// --- Trip Categories (Doughnut) ---
new Chart(document.getElementById('tripCats'), {{
  type: 'doughnut',
  data: {{
    labels: D.tripCats.labels,
    datasets: [{{
      data: D.tripCats.counts,
      backgroundColor: ['#64748b','#0ea5e9','#a78bfa','#f59e0b','#ef4444'],
      borderWidth: 0,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ padding: 12 }} }}
    }}
  }}
}});

// --- Seasonal EV Ratio ---
new Chart(document.getElementById('seasonalEv'), {{
  type: 'bar',
  data: {{
    labels: D.seasonal.labels,
    datasets: [{{
      label: 'EV Ratio (%)',
      data: D.seasonal.ev_ratio,
      backgroundColor: ['#60a5fa','#34d399','#fbbf24','#f97316'],
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ title: {{ display: true, text: '%' }}, grid: {{ color: gridColor }}, max: 100 }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Seasonal Fuel ---
new Chart(document.getElementById('seasonalFuel'), {{
  type: 'bar',
  data: {{
    labels: D.seasonal.labels,
    datasets: [{{
      label: 'L/100km',
      data: D.seasonal.avg_fuel,
      backgroundColor: ['#60a5fa','#34d399','#fbbf24','#f97316'],
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ title: {{ display: true, text: 'L/100km' }}, grid: {{ color: gridColor }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Monthly Score ---
new Chart(document.getElementById('monthlyScore'), {{
  type: 'line',
  data: {{
    labels: D.monthly.labels,
    datasets: [{{
      label: 'Avg Score',
      data: D.monthly.avg_score,
      borderColor: '#a78bfa',
      backgroundColor: 'rgba(167,139,250,0.1)',
      fill: true,
      tension: 0.3,
      pointRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      y: {{ min: 50, max: 100, grid: {{ color: gridColor }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Score Dist ---
new Chart(document.getElementById('scoreDist'), {{
  type: 'bar',
  data: {{
    labels: D.scoreDist.labels,
    datasets: [{{
      label: 'Trips',
      data: D.scoreDist.counts,
      backgroundColor: D.scoreDist.labels.map((_,i) => {{
        const h = (i / D.scoreDist.labels.length) * 120;
        return `hsla(${{h}},70%,50%,0.7)`;
      }}),
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ title: {{ display: true, text: 'Trips' }}, grid: {{ color: gridColor }} }},
      x: {{ title: {{ display: true, text: 'Score Range' }}, grid: {{ display: false }} }}
    }}
  }}
}});

// --- Weekday ---
new Chart(document.getElementById('weekday'), {{
  type: 'bar',
  data: {{
    labels: D.weekdayHour.weekday_labels,
    datasets: [{{
      label: 'Trips',
      data: D.weekdayHour.weekday_trips,
      backgroundColor: 'rgba(14,165,233,0.7)',
      borderRadius: 6,
    }}, {{
      label: 'Distance (km)',
      data: D.weekdayHour.weekday_distance,
      type: 'line',
      borderColor: '#f59e0b',
      yAxisID: 'y1',
      tension: 0.3,
      pointRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      y: {{ title: {{ display: true, text: 'Trips' }}, grid: {{ color: gridColor }} }},
      y1: {{ position: 'right', title: {{ display: true, text: 'km' }}, grid: {{ display: false }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Hourly ---
new Chart(document.getElementById('hourly'), {{
  type: 'bar',
  data: {{
    labels: D.weekdayHour.hour_labels,
    datasets: [{{
      label: 'Trips',
      data: D.weekdayHour.hour_trips,
      backgroundColor: 'rgba(14,165,233,0.7)',
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      y: {{ title: {{ display: true, text: 'Trips' }}, grid: {{ color: gridColor }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Fuel Trend ---
new Chart(document.getElementById('fuelTrend'), {{
  type: 'line',
  data: {{
    labels: D.rollingFuel.map(p => p.x),
    datasets: [{{
      label: 'L/100km (20-trip rolling)',
      data: D.rollingFuel.map(p => p.y),
      borderColor: '#ef4444',
      backgroundColor: 'rgba(239,68,68,0.08)',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      y: {{ title: {{ display: true, text: 'L/100km' }}, grid: {{ color: gridColor }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 15 }} }}
    }}
  }}
}});

// --- Top Trips Table ---
const tbody = document.getElementById('topTripsBody');
D.longestTrips.forEach(t => {{
  const tr = document.createElement('tr');
  tr.className = 'border-b border-gray-800 hover:bg-gray-800/50';
  tr.innerHTML = `
    <td class="py-2 px-3">${{t.date}}</td>
    <td class="text-right py-2 px-3 font-medium text-white">${{t.distance}}</td>
    <td class="text-right py-2 px-3">${{t.duration_min}}</td>
    <td class="text-right py-2 px-3">${{t.fuel}}</td>
    <td class="text-right py-2 px-3">${{t.cost}} PLN</td>
    <td class="text-right py-2 px-3">${{t.ev_pct}}%</td>
    <td class="text-right py-2 px-3">${{t.score ?? '—'}}</td>`;
  tbody.appendChild(tr);
}});
</script>
</body>
</html>"""
    return html


def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        print("Run backfill.py first.")
        raise SystemExit(1)

    conn = sqlite3.connect(DB_PATH)

    print("Loading trips from database...")
    trips = load_trips(conn)
    print(f"  {len(trips)} trips")

    print("Loading heatmap waypoints...")
    heatmap = load_heatmap_points(conn)
    print(f"  {len(heatmap)} clustered grid cells")

    conn.close()

    print("Computing aggregations...")
    kpis = compute_kpis(trips)
    monthly = compute_monthly(trips)
    wh = compute_weekday_hour(trips)
    sd = compute_score_distribution(trips)
    lt = top_trips(trips)
    tc = compute_trip_categories(trips)
    sea = compute_seasonal(trips)

    print("Building HTML...")
    html = build_html(kpis, monthly, wh, sd, heatmap, lt, tc, sea, trips)

    OUTPUT.write_text(html)
    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\nDashboard saved to {OUTPUT} ({size_mb:.1f} MB)")
    print("Open it in your browser!")


if __name__ == "__main__":
    main()
