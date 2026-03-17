"""Build a self-contained HTML commute report from trips.db."""

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fuel_config import (
    COUNTRY_INFO,
    get_country_info,
    get_exchange_rate,
    get_fuel_price,
    load_cache,
    save_cache,
)

DB_PATH = Path(__file__).parent / "trips.db"
DEFAULT_RADIUS = 0.01  # ~1 km
CO2_KG_PER_LITER = 2.31
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_all_vehicles(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute("SELECT vin, alias, brand, fuel_type FROM vehicles").fetchall()
        if rows:
            return [{"vin": r[0], "alias": r[1] or "My Car", "brand": r[2] or "", "fuel_type": r[3] or "gasoline"} for r in rows]
    except sqlite3.OperationalError:
        try:
            rows = conn.execute("SELECT vin, alias, brand FROM vehicles").fetchall()
            if rows:
                return [{"vin": r[0], "alias": r[1] or "My Car", "brand": r[2] or "", "fuel_type": "gasoline"} for r in rows]
        except sqlite3.OperationalError:
            pass
    return []


def load_trips(conn: sqlite3.Connection, vin: str, tz_name: str = "Europe/Warsaw") -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trips WHERE vin = ? ORDER BY trip_start_time", (vin,)
    ).fetchall()
    result = []
    tz = ZoneInfo(tz_name)
    for r in rows:
        d = dict(r)
        start = datetime.fromisoformat(d["trip_start_time"]).astimezone(tz) if d.get("trip_start_time") else None
        if not start:
            continue
        countries_raw = d.get("countries")
        result.append({
            "start": start,
            "end": datetime.fromisoformat(d["trip_end_time"]).astimezone(tz) if d.get("trip_end_time") else None,
            "duration_sec": d.get("duration_sec") or 0,
            "distance_km": d.get("distance_km") or 0,
            "ev_distance_km": d.get("ev_distance_km") or 0,
            "fuel_ml": d.get("fuel_consumed_l") or 0,
            "avg_fuel": d.get("avg_fuel_l100km") or 0,
            "start_lat": d.get("start_lat"),
            "start_lng": d.get("start_lng"),
            "end_lat": d.get("end_lat"),
            "end_lng": d.get("end_lng"),
            "score": d.get("score"),
        })
    conn.row_factory = None
    return result


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _near(lat1, lng1, lat2, lng2, radius: float) -> bool:
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return False
    return abs(lat1 - lat2) <= radius and abs(lng1 - lng2) <= radius


def classify_commute_trips(
    trips: list[dict],
    origin: tuple[float, float],
    destinations: list[tuple[float, float]],
    radius: float,
    weekdays: set[int] | None,
    since: date | None,
) -> dict:
    """
    Returns a dict keyed by destination index (0-based).
    Each value is {"outbound": [...], "return": [...]}.
    """
    result = {i: {"outbound": [], "return": []} for i in range(len(destinations))}
    olat, olng = origin

    for t in trips:
        trip_date = t["start"].date()
        if since is not None and trip_date < since:
            continue
        if weekdays is not None and t["start"].weekday() not in weekdays:
            continue
        for i, (dlat, dlng) in enumerate(destinations):
            if _near(t["start_lat"], t["start_lng"], olat, olng, radius) and \
               _near(t["end_lat"], t["end_lng"], dlat, dlng, radius):
                result[i]["outbound"].append(t)
            elif _near(t["start_lat"], t["start_lng"], dlat, dlng, radius) and \
                 _near(t["end_lat"], t["end_lng"], olat, olng, radius):
                result[i]["return"].append(t)

    return result


def build_commute_days(classified: dict, dest_labels: list[str]) -> dict:
    """
    Returns dict[date_str -> commute_day_info].
    Each date gets one entry; if multiple destinations used that day, first found wins for 'destination_label'.
    """
    by_date = {}

    for i, label in enumerate(dest_labels):
        for t in classified[i]["outbound"]:
            ds = t["start"].date().isoformat()
            if ds not in by_date:
                by_date[ds] = {
                    "date": t["start"].date(),
                    "weekday_name": WEEKDAY_NAMES[t["start"].weekday()],
                    "destination_label": label,
                    "dest_idx": i,
                    "outbound": None,
                    "return_trip": None,
                }
            if by_date[ds]["outbound"] is None:
                by_date[ds]["outbound"] = t
                by_date[ds]["destination_label"] = label
                by_date[ds]["dest_idx"] = i

        for t in classified[i]["return"]:
            ds = t["start"].date().isoformat()
            if ds not in by_date:
                by_date[ds] = {
                    "date": t["start"].date(),
                    "weekday_name": WEEKDAY_NAMES[t["start"].weekday()],
                    "destination_label": label,
                    "dest_idx": i,
                    "outbound": None,
                    "return_trip": None,
                }
            if by_date[ds]["return_trip"] is None:
                by_date[ds]["return_trip"] = t

    # Mark complete days
    for v in by_date.values():
        v["complete"] = (v["outbound"] is not None and v["return_trip"] is not None)

    return dict(sorted(by_date.items()))


def compute_commute_kpis(
    commute_days: dict,
    classified: dict,
    dest_labels: list[str],
    price_fn,
    fuel_type: str,
) -> dict:
    all_commute_trips = []
    for d in commute_days.values():
        if d["outbound"]:
            all_commute_trips.append(d["outbound"])
        if d["return_trip"]:
            all_commute_trips.append(d["return_trip"])

    total_dist = sum(t["distance_km"] for t in all_commute_trips)
    total_fuel = sum(t["fuel_ml"] for t in all_commute_trips)
    total_dur_sec = sum(t["duration_sec"] for t in all_commute_trips)
    total_ev_km = sum(t["ev_distance_km"] for t in all_commute_trips)

    total_cost = 0.0
    for t in all_commute_trips:
        month = t["start"].strftime("%Y-%m")
        price = price_fn(month)
        total_cost += t["fuel_ml"] * price

    co2 = round(total_fuel * CO2_KG_PER_LITER, 1)
    ev_ratio = round(total_ev_km / total_dist * 100, 1) if total_dist > 0 else 0.0
    scores = [t["score"] for t in all_commute_trips if t["score"] is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    # Per-destination counts
    per_dest = {}
    for i, label in enumerate(dest_labels):
        days_with_dest = [d for d in commute_days.values() if d["dest_idx"] == i]
        per_dest[label] = {
            "days": len(days_with_dest),
            "outbound": len(classified[i]["outbound"]),
            "return": len(classified[i]["return"]),
        }

    complete_days = [d for d in commute_days.values() if d["complete"]]
    dates = sorted(commute_days.keys())

    return {
        "total_commute_days": len(commute_days),
        "complete_round_trips": len(complete_days),
        "per_destination": per_dest,
        "total_distance_km": round(total_dist, 1),
        "avg_distance_km": round(total_dist / len(all_commute_trips), 1) if all_commute_trips else 0,
        "total_fuel_l": round(total_fuel, 1),
        "total_duration_h": round(total_dur_sec / 3600, 1),
        "avg_duration_min": round(total_dur_sec / 60 / len(all_commute_trips), 0) if all_commute_trips else 0,
        "total_cost": round(total_cost, 2),
        "co2_kg": co2,
        "ev_ratio_pct": ev_ratio,
        "avg_score": avg_score,
        "first_date": dates[0] if dates else "N/A",
        "last_date": dates[-1] if dates else "N/A",
        "total_trips": len(all_commute_trips),
    }


def compute_monthly_commute(commute_days: dict) -> dict:
    monthly: dict[str, dict] = {}
    for ds, d in commute_days.items():
        month = ds[:7]
        if month not in monthly:
            monthly[month] = {"days": 0, "dist": 0.0, "outbound": 0, "returns": 0}
        monthly[month]["days"] += 1
        if d["outbound"]:
            monthly[month]["dist"] += d["outbound"]["distance_km"]
            monthly[month]["outbound"] += 1
        if d["return_trip"]:
            monthly[month]["dist"] += d["return_trip"]["distance_km"]
            monthly[month]["returns"] += 1
    labels = sorted(monthly.keys())
    return {
        "labels": labels,
        "days": [monthly[m]["days"] for m in labels],
        "dist": [round(monthly[m]["dist"], 1) for m in labels],
        "outbound": [monthly[m]["outbound"] for m in labels],
        "returns": [monthly[m]["returns"] for m in labels],
    }


def compute_destination_split(commute_days: dict, dest_labels: list[str]) -> dict:
    counts = defaultdict(int)
    for d in commute_days.values():
        counts[d["destination_label"]] += 1
    return {
        "labels": dest_labels,
        "counts": [counts[lbl] for lbl in dest_labels],
    }


def compute_day_breakdown(commute_days: dict) -> dict:
    counts = defaultdict(int)
    for d in commute_days.values():
        counts[d["weekday_name"]] += 1
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return {
        "labels": days,
        "counts": [counts[d] for d in days],
    }


def _bucket_time(dt: datetime, bucket_min: int) -> str:
    total = dt.hour * 60 + dt.minute
    b = (total // bucket_min) * bucket_min
    h, m = divmod(b, 60)
    return f"{h:02d}:{m:02d}"


def compute_departure_histogram(trips: list[dict], bucket_minutes: int = 15) -> dict:
    counts: dict[str, int] = defaultdict(int)
    for t in trips:
        counts[_bucket_time(t["start"], bucket_minutes)] += 1
    labels = sorted(counts.keys())
    return {"labels": labels, "counts": [counts[lbl] for lbl in labels]}


def compute_duration_trends(commute_days: dict) -> dict:
    out_dates, out_dur = [], []
    ret_dates, ret_dur = [], []
    for ds, d in sorted(commute_days.items()):
        if d["outbound"]:
            out_dates.append(ds)
            out_dur.append(round(d["outbound"]["duration_sec"] / 60, 1))
        if d["return_trip"]:
            ret_dates.append(ds)
            ret_dur.append(round(d["return_trip"]["duration_sec"] / 60, 1))
    return {
        "outbound_dates": out_dates,
        "outbound_min": out_dur,
        "return_dates": ret_dates,
        "return_min": ret_dur,
    }


def compute_duration_trends_from_trips(outbound_trips: list[dict], return_trips: list[dict]) -> dict:
    out_sorted = sorted(outbound_trips, key=lambda t: t["start"])
    ret_sorted = sorted(return_trips, key=lambda t: t["start"])
    return {
        "outbound_dates": [t["start"].date().isoformat() for t in out_sorted],
        "outbound_min": [round(t["duration_sec"] / 60, 1) for t in out_sorted],
        "return_dates": [t["start"].date().isoformat() for t in ret_sorted],
        "return_min": [round(t["duration_sec"] / 60, 1) for t in ret_sorted],
    }


def _rolling_avg(trips: list[dict], window: int = 10) -> list[dict]:
    result = []
    for i in range(len(trips)):
        chunk = trips[max(0, i - window + 1):i + 1]
        dist = sum(t["distance_km"] for t in chunk)
        fuel = sum(t["fuel_ml"] for t in chunk)
        if dist > 0:
            result.append({"x": trips[i]["start"].strftime("%Y-%m-%d"), "y": round(fuel / dist * 100, 2)})
    return result


def compute_fuel_trend(all_commute_trips: list[dict]) -> dict:
    pts = _rolling_avg(all_commute_trips, window=10)
    return {"dates": [p["x"] for p in pts], "values": [p["y"] for p in pts]}


def compute_ev_trend(all_commute_trips: list[dict]) -> dict:
    result = []
    for t in all_commute_trips:
        if t["distance_km"] > 0:
            result.append({
                "x": t["start"].strftime("%Y-%m-%d"),
                "y": round(t["ev_distance_km"] / t["distance_km"] * 100, 1),
            })
    return {"dates": [p["x"] for p in result], "values": [p["y"] for p in result]}


def compute_best_times(outbound_trips: list[dict], return_trips: list[dict], bucket_minutes: int = 15) -> dict:
    def _analyze(trips):
        buckets: dict[str, list[float]] = defaultdict(list)
        for t in trips:
            b = _bucket_time(t["start"], bucket_minutes)
            buckets[b].append(t["duration_sec"] / 60)
        MIN_BEST_COUNT = 3
        labels = sorted(buckets.keys())
        avgs = [round(sum(buckets[lbl]) / len(buckets[lbl]), 1) for lbl in labels]
        counts = [len(buckets[lbl]) for lbl in labels]
        candidates = [(lbl, avg) for lbl, avg, cnt in zip(labels, avgs, counts) if cnt >= MIN_BEST_COUNT]
        best = min(candidates, key=lambda x: x[1])[0] if candidates else None
        return {"labels": labels, "avg_min": avgs, "counts": counts, "best": best, "min_count": MIN_BEST_COUNT}

    return {
        "outbound": _analyze(outbound_trips),
        "return": _analyze(return_trips),
    }


def compute_missing_days(
    commute_days: dict,
    since: date | None,
    weekdays: set[int] | None,
    today: date,
) -> list[dict]:
    if since is None:
        return []
    result = []
    cur = since
    while cur <= today:
        if weekdays is None or cur.weekday() in weekdays:
            ds = cur.isoformat()
            if ds not in commute_days:
                result.append({"date": ds, "weekday": WEEKDAY_NAMES[cur.weekday()]})
        cur += timedelta(days=1)
    return result


def build_trip_log(commute_days: dict, dest_labels: list[str], price_fn) -> list[dict]:
    rows = []
    for ds, d in sorted(commute_days.items()):
        for direction, trip in [("outbound", d["outbound"]), ("return", d["return_trip"])]:
            if trip is None:
                continue
            month = trip["start"].strftime("%Y-%m")
            price = price_fn(month)
            cost = round(trip["fuel_ml"] * price, 2)
            rows.append({
                "date": ds,
                "weekday": d["weekday_name"],
                "destination": d["destination_label"],
                "direction": direction,
                "departure": trip["start"].strftime("%H:%M"),
                "duration_min": round(trip["duration_sec"] / 60, 0),
                "distance_km": round(trip["distance_km"], 1),
                "fuel_l": round(trip["fuel_ml"], 2),
                "cost": cost,
                "score": trip["score"],
            })
    return rows


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def build_commute_html(
    data: dict,
    vehicle: dict,
    currency_code: str,
    currency_symbol: str,
    origin: tuple[float, float],
    destinations: list[tuple[float, float]],
    dest_labels: list[str],
    origin_label: str = "Home",
) -> str:
    vehicle_name = vehicle.get("alias", "My Car")
    kpis = data["kpis"]
    dest_cards = ""
    for lbl, stats in kpis["per_destination"].items():
        dest_cards += f"""
    <div class="card">
      <div class="kpi-value">{stats['days']}</div>
      <div class="kpi-label">{lbl} days</div>
    </div>"""

    # Best times from first destination for KPI cards
    first_lbl = dest_labels[0] if dest_labels else None
    first_timing = data["perDestTiming"].get(first_lbl, {}) if first_lbl else {}
    first_best = first_timing.get("bestTimes", {})
    best_out = (first_best.get("outbound") or {}).get("best") or "N/A"
    best_ret = (first_best.get("return") or {}).get("best") or "N/A"
    best_label_suffix = f" ({first_lbl})" if len(dest_labels) > 1 and first_lbl else ""

    data_json = json.dumps(data, separators=(",", ":"))

    dest_info = " | ".join(
        f"{lbl}: {lat},{lng}" for lbl, (lat, lng) in zip(dest_labels, destinations)
    )
    origin_info = f"{origin_label} ({origin[0]},{origin[1]})"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{vehicle_name} Commute Report</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
<script>
tailwind.config = {{
  darkMode: 'class',
  theme: {{ extend: {{ colors: {{ lexus: {{ 500:'#917f65', 800:'#5a4e41' }} }} }} }}
}}
</script>
<script>
(function(){{
  var d=localStorage.getItem('theme');
  if(d==='dark'||(d!=='light'&&window.matchMedia('(prefers-color-scheme:dark)').matches))
    document.documentElement.classList.add('dark');
}})();
</script>
<style>
  :root {{
    --bg-body:#f8fafc;--bg-card:#ffffff;--border-card:#e2e8f0;
    --text-heading:#1e293b;--text-body:#334155;--text-muted:#64748b;
    --text-faint:#94a3b8;--chart-grid:rgba(0,0,0,0.06);--chart-tick:#64748b;
    --border-table:#e2e8f0;--bg-hover:#f1f5f9;
  }}
  :root.dark {{
    --bg-body:#030712;--bg-card:rgba(31,41,55,0.5);--border-card:rgba(55,65,81,0.5);
    --text-heading:#ffffff;--text-body:#e5e7eb;--text-muted:#9ca3af;
    --text-faint:#6b7280;--chart-grid:rgba(255,255,255,0.06);--chart-tick:#9ca3af;
    --border-table:#1f2937;--bg-hover:rgba(31,41,55,0.5);
  }}
  body {{ font-family:'Inter',system-ui,-apple-system,sans-serif;background:var(--bg-body);color:var(--text-body);transition:background-color 0.3s,color 0.2s; }}
  .card {{ background:var(--bg-card);backdrop-filter:blur(12px);border-radius:1rem;border:1px solid var(--border-card);padding:1.5rem;transition:background-color 0.3s,border-color 0.2s; }}
  .kpi-value {{ font-size:1.875rem;line-height:2.25rem;font-weight:700;color:var(--text-heading); }}
  .kpi-label {{ font-size:0.875rem;color:var(--text-muted);margin-top:0.25rem; }}
  .text-heading {{ color:var(--text-heading); }}
  .text-muted {{ color:var(--text-muted); }}
  .text-faint {{ color:var(--text-faint); }}
  .border-themed {{ border-color:var(--border-table); }}
  .row-hover:hover {{ background:var(--bg-hover); }}
  .tab-btn {{ padding:0.5rem 1rem;border-radius:0.5rem;font-size:0.875rem;font-weight:500;transition:all 0.15s;color:var(--text-muted);cursor:pointer;border:none;background:transparent; }}
  .tab-btn.active {{ background:#917f65;color:#fff; }}
</style>
</head>
<body class="min-h-screen">
<div class="max-w-7xl mx-auto px-4 py-8">

  <!-- Header -->
  <div class="flex items-center justify-between mb-8">
    <div>
      <h1 class="text-3xl font-bold text-heading tracking-tight">{vehicle_name} Commute Report</h1>
      <p class="text-muted mt-1">{kpis['first_date']} &mdash; {kpis['last_date']} &middot; From {origin_info} &middot; {dest_info}</p>
    </div>
    <div class="flex items-center gap-4">
      <button onclick="applyTheme(!isDarkMode())" class="p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors" title="Toggle theme">
        <svg id="sunIcon" class="w-5 h-5 text-heading hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
        </svg>
        <svg id="moonIcon" class="w-5 h-5 text-heading hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
        </svg>
      </button>
      <div class="text-right text-sm text-faint">
        <div>{kpis['total_trips']} commute trips</div>
      </div>
    </div>
  </div>

  <!-- KPI Row 1 -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-4">
    <div class="card">
      <div class="kpi-value">{kpis['total_commute_days']}</div>
      <div class="kpi-label">Commute Days</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['complete_round_trips']}</div>
      <div class="kpi-label">Round Trips</div>
    </div>{dest_cards}
    <div class="card">
      <div class="kpi-value">{kpis['total_distance_km']:,.1f}<span class="text-lg text-muted"> km</span></div>
      <div class="kpi-label">Total Distance</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['avg_distance_km']}<span class="text-lg text-muted"> km</span></div>
      <div class="kpi-label">Avg per Trip</div>
    </div>
  </div>

  <!-- KPI Row 2 -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-4">
    <div class="card">
      <div class="kpi-value">{kpis['total_fuel_l']:,.1f}<span class="text-lg text-muted"> L</span></div>
      <div class="kpi-label">Total Fuel</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_cost']:,.2f}<span class="text-lg text-muted"> {currency_code}</span></div>
      <div class="kpi-label">Total Fuel Cost</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['co2_kg']:,.1f}<span class="text-lg text-muted"> kg</span></div>
      <div class="kpi-label">CO2 Emitted</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['ev_ratio_pct']}<span class="text-lg text-muted">%</span></div>
      <div class="kpi-label">EV Ratio</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['avg_score'] if kpis['avg_score'] is not None else 'N/A'}</div>
      <div class="kpi-label">Avg Driving Score</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_duration_h']:,.1f}<span class="text-lg text-muted"> h</span></div>
      <div class="kpi-label">Total Drive Time</div>
    </div>
  </div>

  <!-- KPI Row 3 - Best Times -->
  <div class="grid grid-cols-2 md:grid-cols-2 gap-4 mb-8">
    <div class="card">
      <div class="kpi-value" style="color:#22c55e">{best_out}</div>
      <div class="kpi-label">Best Morning Departure{best_label_suffix} (≥3 trips, shortest avg)</div>
    </div>
    <div class="card">
      <div class="kpi-value" style="color:#3b82f6">{best_ret}</div>
      <div class="kpi-label">Best Return Departure{best_label_suffix} (≥3 trips, shortest avg)</div>
    </div>
  </div>

  <!-- Tabs -->
  <div class="flex gap-2 mb-6 flex-wrap">
    <button class="tab-btn active" onclick="switchTab('overview')">Overview</button>
    <button class="tab-btn" onclick="switchTab('timing')">Timing</button>
    <button class="tab-btn" onclick="switchTab('efficiency')">Efficiency</button>
    <button class="tab-btn" onclick="switchTab('triplog')">Trip Log</button>
  </div>

  <!-- Tab: Overview -->
  <div id="tab-overview">
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
      <div class="card">
        <h3 class="text-heading font-semibold mb-4">Monthly Summary</h3>
        <canvas id="monthlyChart" height="200"></canvas>
      </div>
      <div class="card">
        <h3 class="text-heading font-semibold mb-4">Destination Split</h3>
        <canvas id="destSplit" height="200"></canvas>
      </div>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
      <div class="card">
        <h3 class="text-heading font-semibold mb-4">Day of Week Breakdown</h3>
        <canvas id="dowChart" height="200"></canvas>
      </div>
      <div class="card">
        <h3 class="text-heading font-semibold mb-4">Missing Days (WFH / Holiday)</h3>
        <div class="overflow-auto max-h-72">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-muted border-b border-themed">
                <th class="py-2 pr-4">Date</th>
                <th class="py-2">Weekday</th>
              </tr>
            </thead>
            <tbody id="missingDaysBody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- Tab: Timing -->
  <div id="tab-timing" style="display:none">
    <div id="timingDestSections"></div>
  </div>

  <!-- Tab: Efficiency -->
  <div id="tab-efficiency" style="display:none">
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
      <div class="card">
        <h3 class="text-heading font-semibold mb-4">Fuel Efficiency Trend (L/100km)</h3>
        <canvas id="fuelTrend" height="200"></canvas>
      </div>
      <div class="card">
        <h3 class="text-heading font-semibold mb-4">EV Ratio Trend (%)</h3>
        <canvas id="evTrend" height="200"></canvas>
      </div>
    </div>
  </div>

  <!-- Tab: Trip Log -->
  <div id="tab-triplog" style="display:none">
    <div class="card">
      <h3 class="text-heading font-semibold mb-4">All Commute Trips</h3>
      <div class="overflow-auto">
        <table class="w-full text-sm" id="tripLogTable">
          <thead>
            <tr class="text-left text-muted border-b border-themed">
              <th class="py-2 pr-3 cursor-pointer hover:text-heading" onclick="sortTable(0)">Date &#8597;</th>
              <th class="py-2 pr-3 cursor-pointer hover:text-heading" onclick="sortTable(1)">Day &#8597;</th>
              <th class="py-2 pr-3 cursor-pointer hover:text-heading" onclick="sortTable(2)">Dest &#8597;</th>
              <th class="py-2 pr-3 cursor-pointer hover:text-heading" onclick="sortTable(3)">Dir &#8597;</th>
              <th class="py-2 pr-3 cursor-pointer hover:text-heading" onclick="sortTable(4)">Depart &#8597;</th>
              <th class="py-2 pr-3 cursor-pointer hover:text-heading" onclick="sortTable(5)">Dur (min) &#8597;</th>
              <th class="py-2 pr-3 cursor-pointer hover:text-heading" onclick="sortTable(6)">Dist (km) &#8597;</th>
              <th class="py-2 pr-3 cursor-pointer hover:text-heading" onclick="sortTable(7)">Fuel (L) &#8597;</th>
              <th class="py-2 pr-3 cursor-pointer hover:text-heading" onclick="sortTable(8)">Cost &#8597;</th>
              <th class="py-2 cursor-pointer hover:text-heading" onclick="sortTable(9)">Score &#8597;</th>
            </tr>
          </thead>
          <tbody id="tripLogBody"></tbody>
        </table>
      </div>
    </div>
  </div>

</div><!-- end max-w-7xl -->

<script>
const D = {data_json};

// --- Theme ---
function isDarkMode() {{ return document.documentElement.classList.contains('dark'); }}
function getThemeColors() {{
  const s = getComputedStyle(document.documentElement);
  return {{
    grid: s.getPropertyValue('--chart-grid').trim(),
    tick: s.getPropertyValue('--chart-tick').trim(),
  }};
}}

function applyTheme(dark) {{
  document.documentElement.classList.toggle('dark', dark);
  localStorage.setItem('theme', dark ? 'dark' : 'light');
  document.getElementById('sunIcon').classList.toggle('hidden', !dark);
  document.getElementById('moonIcon').classList.toggle('hidden', dark);
  charts.forEach(c => {{
    const tc = getThemeColors();
    if (c.options.scales) {{
      Object.values(c.options.scales).forEach(ax => {{
        if (ax.grid) ax.grid.color = tc.grid;
        if (ax.ticks) ax.ticks.color = tc.tick;
      }});
    }}
    c.update('none');
  }});
}}

(function() {{
  const dark = isDarkMode();
  document.getElementById('sunIcon').classList.toggle('hidden', !dark);
  document.getElementById('moonIcon').classList.toggle('hidden', dark);
}})();

// --- Tab Navigation ---
function switchTab(name) {{
  ['overview','timing','efficiency','triplog'].forEach(t => {{
    document.getElementById('tab-' + t).style.display = (t === name) ? '' : 'none';
  }});
  document.querySelectorAll('.tab-btn').forEach(btn => {{
    const active = btn.getAttribute('onclick') === "switchTab('" + name + "')";
    btn.classList.toggle('active', active);
  }});
}}

// --- Charts ---
const charts = [];
function createChart(id, config) {{
  const c = new Chart(document.getElementById(id), config);
  charts.push(c);
  return c;
}}

function baseScales(xTitle, yTitle) {{
  const tc = getThemeColors();
  return {{
    x: {{
      title: {{ display: !!xTitle, text: xTitle, color: tc.tick }},
      grid: {{ color: tc.grid }},
      ticks: {{ color: tc.tick }},
    }},
    y: {{
      title: {{ display: !!yTitle, text: yTitle, color: tc.tick }},
      grid: {{ color: tc.grid }},
      ticks: {{ color: tc.tick }},
    }},
  }};
}}

// Monthly summary
createChart('monthlyChart', {{
  type: 'bar',
  data: {{
    labels: D.monthly.labels,
    datasets: [
      {{ type: 'bar', label: 'Commute Days', data: D.monthly.days, backgroundColor: 'rgba(145,127,101,0.7)', yAxisID: 'y' }},
      {{ type: 'line', label: 'Total Distance (km)', data: D.monthly.dist, borderColor: '#3b82f6', backgroundColor: 'transparent', tension: 0.3, yAxisID: 'y2' }},
    ],
  }},
  options: {{
    responsive: true,
    scales: {{
      ...baseScales('', ''),
      y: {{ ...baseScales('','').y, position: 'left', title: {{ display: true, text: 'Days' }} }},
      y2: {{ ...baseScales('','').y, position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'km' }} }},
    }},
  }},
}});

// Destination split
createChart('destSplit', {{
  type: 'doughnut',
  data: {{
    labels: D.destSplit.labels,
    datasets: [{{ data: D.destSplit.counts, backgroundColor: ['#917f65','#3b82f6','#22c55e','#f59e0b'] }}],
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom' }} }} }},
}});

// Day of week
createChart('dowChart', {{
  type: 'bar',
  data: {{
    labels: D.dayBreakdown.labels,
    datasets: [{{ label: 'Commute Trips', data: D.dayBreakdown.counts, backgroundColor: 'rgba(145,127,101,0.7)' }}],
  }},
  options: {{ responsive: true, scales: baseScales('', 'Trips') }},
}});

// Per-destination timing sections
(function() {{
  const container = document.getElementById('timingDestSections');
  const showHeading = D.destLabels.length > 1;

  function makeBestTimeChart(canvasId, bt, highlightColor) {{
    const colors = bt.labels.map((lbl, i) => {{
      if (lbl === bt.best) return highlightColor;
      if (bt.counts[i] < bt.min_count) return 'rgba(145,127,101,0.15)';
      return 'rgba(145,127,101,0.5)';
    }});
    const countPlugin = {{
      afterDatasetsDraw(chart) {{
        const ctx = chart.ctx;
        chart.data.datasets[0].data.forEach((val, i) => {{
          const meta = chart.getDatasetMeta(0).data[i];
          ctx.fillStyle = getThemeColors().tick;
          ctx.font = '10px sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText('n=' + bt.counts[i], meta.x, meta.y - 5);
        }});
      }}
    }};
    createChart(canvasId, {{
      type: 'bar',
      data: {{
        labels: bt.labels,
        datasets: [{{ label: 'Avg Duration (min)', data: bt.avg_min, backgroundColor: colors }}],
      }},
      options: {{
        responsive: true,
        scales: baseScales('Time', 'Avg min'),
        plugins: {{
          tooltip: {{ callbacks: {{ label: ctx => 'Avg: ' + ctx.raw + ' min (' + bt.counts[ctx.dataIndex] + ' trips)' }} }},
        }},
      }},
      plugins: [countPlugin],
    }});
  }}

  D.destLabels.forEach((lbl, i) => {{
    const pd = D.perDestTiming[lbl];
    const section = document.createElement('div');
    section.innerHTML = `
      ${{showHeading ? `<h2 class="text-heading font-semibold text-lg mb-4">${{lbl}}</h2>` : ''}}
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div class="card">
          <h3 class="text-heading font-semibold mb-4">Morning Departure Times</h3>
          <canvas id="morningHist_${{i}}" height="200"></canvas>
        </div>
        <div class="card">
          <h3 class="text-heading font-semibold mb-4">Return Departure Times</h3>
          <canvas id="returnHist_${{i}}" height="200"></canvas>
        </div>
      </div>
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div class="card">
          <h3 class="text-heading font-semibold mb-4">Best Departure Window (Morning)</h3>
          <canvas id="bestOutChart_${{i}}" height="200"></canvas>
        </div>
        <div class="card">
          <h3 class="text-heading font-semibold mb-4">Best Departure Window (Return)</h3>
          <canvas id="bestRetChart_${{i}}" height="200"></canvas>
        </div>
      </div>
      <div class="card mb-6">
        <h3 class="text-heading font-semibold mb-4">Trip Duration Over Time</h3>
        <canvas id="durationTrend_${{i}}" height="160"></canvas>
      </div>`;
    container.appendChild(section);

    createChart('morningHist_' + i, {{
      type: 'bar',
      data: {{
        labels: pd.morningHist.labels,
        datasets: [{{ label: 'Departures', data: pd.morningHist.counts, backgroundColor: 'rgba(34,197,94,0.7)' }}],
      }},
      options: {{ responsive: true, scales: baseScales('Time', 'Count') }},
    }});

    createChart('returnHist_' + i, {{
      type: 'bar',
      data: {{
        labels: pd.returnHist.labels,
        datasets: [{{ label: 'Departures', data: pd.returnHist.counts, backgroundColor: 'rgba(59,130,246,0.7)' }}],
      }},
      options: {{ responsive: true, scales: baseScales('Time', 'Count') }},
    }});

    makeBestTimeChart('bestOutChart_' + i, pd.bestTimes.outbound, '#22c55e');
    makeBestTimeChart('bestRetChart_' + i, pd.bestTimes.return, '#3b82f6');

    const dt = pd.durationTrends;
    createChart('durationTrend_' + i, {{
      type: 'line',
      data: {{
        datasets: [
          {{ label: 'Outbound (min)', data: dt.outbound_dates.map((d,j) => ({{x:d,y:dt.outbound_min[j]}})), borderColor: '#22c55e', backgroundColor: 'transparent', tension: 0.2, pointRadius: 3 }},
          {{ label: 'Return (min)', data: dt.return_dates.map((d,j) => ({{x:d,y:dt.return_min[j]}})), borderColor: '#3b82f6', backgroundColor: 'transparent', tension: 0.2, pointRadius: 3 }},
        ],
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ type: 'time', time: {{ unit: 'month' }}, ...baseScales('','').x }},
          y: {{ ...baseScales('','').y, title: {{ display: true, text: 'Minutes' }} }},
        }},
      }},
    }});
  }});
}})();

// Fuel trend
createChart('fuelTrend', {{
  type: 'line',
  data: {{
    datasets: [{{ label: 'L/100km (rolling avg)', data: D.fuelTrend.dates.map((d,i) => ({{x:d,y:D.fuelTrend.values[i]}})), borderColor: '#f59e0b', backgroundColor: 'transparent', tension: 0.3 }}],
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ type: 'time', time: {{ unit: 'month' }}, ...baseScales('','').x }},
      y: {{ ...baseScales('','').y, title: {{ display: true, text: 'L/100km' }} }},
    }},
  }},
}});

// EV trend
createChart('evTrend', {{
  type: 'line',
  data: {{
    datasets: [{{ label: 'EV %', data: D.evTrend.dates.map((d,i) => ({{x:d,y:D.evTrend.values[i]}})), borderColor: '#22c55e', backgroundColor: 'transparent', tension: 0.3 }}],
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ type: 'time', time: {{ unit: 'month' }}, ...baseScales('','').x }},
      y: {{ ...baseScales('','').y, title: {{ display: true, text: '%' }}, min: 0, max: 100 }},
    }},
  }},
}});

// Missing days table
(function() {{
  const tbody = document.getElementById('missingDaysBody');
  D.missingDays.forEach(r => {{
    const tr = document.createElement('tr');
    tr.className = 'row-hover border-b border-themed';
    tr.innerHTML = `<td class="py-2 pr-4">${{r.date}}</td><td class="py-2 text-muted">${{r.weekday}}</td>`;
    tbody.appendChild(tr);
  }});
  if (!D.missingDays.length) {{
    tbody.innerHTML = '<tr><td colspan="2" class="py-4 text-center text-muted">No missing days found</td></tr>';
  }}
}})();

// Trip log table
(function() {{
  const tbody = document.getElementById('tripLogBody');
  D.tripLog.forEach(r => {{
    const tr = document.createElement('tr');
    tr.className = 'row-hover border-b border-themed';
    const score = r.score !== null && r.score !== undefined ? r.score : '-';
    tr.innerHTML = `
      <td class="py-2 pr-3">${{r.date}}</td>
      <td class="py-2 pr-3 text-muted">${{r.weekday.slice(0,3)}}</td>
      <td class="py-2 pr-3">${{r.destination}}</td>
      <td class="py-2 pr-3 ${{r.direction==='outbound'?'text-green-500':'text-blue-500'}}">${{r.direction==='outbound'?'&#8599;':'&#8600;'}}</td>
      <td class="py-2 pr-3">${{r.departure}}</td>
      <td class="py-2 pr-3">${{r.duration_min}}</td>
      <td class="py-2 pr-3">${{r.distance_km}}</td>
      <td class="py-2 pr-3">${{r.fuel_l}}</td>
      <td class="py-2 pr-3">${{r.cost}}</td>
      <td class="py-2">${{score}}</td>`;
    tbody.appendChild(tr);
  }});
}})();

// Sort table
let _sortCol = -1, _sortAsc = true;
function sortTable(col) {{
  const tbody = document.getElementById('tripLogBody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  if (_sortCol === col) _sortAsc = !_sortAsc; else {{ _sortCol = col; _sortAsc = true; }}
  rows.sort((a, b) => {{
    const av = a.cells[col].textContent.trim();
    const bv = b.cells[col].textContent.trim();
    const an = parseFloat(av.replace(/[^0-9.-]/g,'')), bn = parseFloat(bv.replace(/[^0-9.-]/g,''));
    const cmp = isNaN(an) || isNaN(bn) ? av.localeCompare(bv) : an - bn;
    return _sortAsc ? cmp : -cmp;
  }});
  rows.forEach(r => tbody.appendChild(r));
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_coord(s: str, name: str) -> tuple[float, float]:
    try:
        parts = s.split(",")
        if len(parts) != 2:
            raise ValueError
        lat, lng = float(parts[0].strip()), float(parts[1].strip())
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            raise ValueError(f"Coordinates out of range for {name}: lat={lat}, lng={lng}")
        return lat, lng
    except (ValueError, AttributeError) as e:
        raise SystemExit(f"Invalid coordinate '{s}' for {name}: {e}") from e


def parse_date(s: str, name: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise SystemExit(f"Invalid date '{s}' for {name} (expected YYYY-MM-DD)") from e


def main() -> None:
    parser = argparse.ArgumentParser(description="Build commute analytics report.")
    parser.add_argument("--from", dest="origin", required=True, metavar="LAT,LNG",
                        help="Origin location (e.g. home): lat,lng")
    parser.add_argument("--to", dest="destinations", required=True, action="append", metavar="LAT,LNG",
                        help="Destination location(s). Repeat for multiple.")
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS,
                        help=f"Coordinate match radius in degrees (default: {DEFAULT_RADIUS})")
    parser.add_argument("--days", default=None,
                        help="Comma-separated weekday numbers 0=Mon..6=Sun (default: all)")
    parser.add_argument("--since", default=None,
                        help="Only include trips from this date (YYYY-MM-DD)")
    parser.add_argument("--country", default="PL",
                        help="ISO country code for fuel prices (default: PL)")
    parser.add_argument("--currency", default=None,
                        help="Display currency code (default: country native)")
    parser.add_argument("--from-name", default="Home",
                        help="Label for origin location (default: Home)")
    parser.add_argument("--to-name", dest="dest_names", action="append", default=None,
                        metavar="NAME", help="Label for --to destination (in order)")
    args = parser.parse_args()

    origin = parse_coord(args.origin, "--from")
    destinations = [parse_coord(d, f"--to #{i+1}") for i, d in enumerate(args.destinations)]
    if args.dest_names and len(args.dest_names) == len(destinations):
        dest_labels = args.dest_names
    else:
        dest_labels = [f"Destination {i+1}" for i in range(len(destinations))]
    origin_label = args.from_name

    weekdays: set[int] | None = None
    if args.days is not None:
        try:
            weekdays = {int(x.strip()) for x in args.days.split(",") if x.strip()}
        except ValueError as e:
            raise SystemExit(f"Invalid --days value: {e}") from e

    since: date | None = None
    if args.since:
        since = parse_date(args.since, "--since")

    country_code = args.country.upper()
    country_info = get_country_info(country_code)
    tz_name = country_info.get("tz", "Europe/Warsaw")
    native_currency = country_info["currency"]
    currency_code = args.currency.upper() if args.currency else native_currency
    currency_symbol = country_info["symbol"]
    if currency_code != native_currency:
        for info in COUNTRY_INFO.values():
            if info["currency"] == currency_code:
                currency_symbol = info["symbol"]
                break

    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    vehicles = load_all_vehicles(conn)
    if not vehicles:
        raise SystemExit("No vehicles found in trips.db. Run backfill.py first.")

    cache = load_cache()
    exchange_rate = get_exchange_rate(native_currency, currency_code, cache)

    def price_fn(month: str) -> float:
        fuel_type = vehicles[0].get("fuel_type", "gasoline")
        native_price = get_fuel_price(country_code, month, fuel_type, conn=conn, cache=cache)
        return round(native_price * exchange_rate, 2)

    # Use first vehicle
    vehicle = vehicles[0]
    vin = vehicle["vin"]
    fuel_type = vehicle.get("fuel_type", "gasoline")
    print(f"Vehicle: {vehicle['alias']} ({vin}), fuel: {fuel_type}")
    print(f"Loading trips...")

    trips = load_trips(conn, vin, tz_name)
    print(f"  {len(trips)} trips total")

    classified = classify_commute_trips(trips, origin, destinations, args.radius, weekdays, since)

    for i, lbl in enumerate(dest_labels):
        print(f"  {lbl} ({destinations[i]}): {len(classified[i]['outbound'])} outbound, {len(classified[i]['return'])} return")

    commute_days = build_commute_days(classified, dest_labels)
    print(f"  {len(commute_days)} commute days identified")

    all_commute_trips = []
    for d in commute_days.values():
        if d["outbound"]:
            all_commute_trips.append(d["outbound"])
        if d["return_trip"]:
            all_commute_trips.append(d["return_trip"])

    all_commute_trips_flat = all_commute_trips

    print("Computing analytics...")
    kpis = compute_commute_kpis(commute_days, classified, dest_labels, price_fn, fuel_type)
    monthly = compute_monthly_commute(commute_days)
    dest_split = compute_destination_split(commute_days, dest_labels)
    day_breakdown = compute_day_breakdown(commute_days)
    fuel_trend = compute_fuel_trend(all_commute_trips_flat)
    ev_trend = compute_ev_trend(all_commute_trips_flat)
    today = datetime.now().date()
    missing_days = compute_missing_days(commute_days, since, weekdays, today)
    trip_log = build_trip_log(commute_days, dest_labels, price_fn)

    per_dest_timing = {}
    for i, lbl in enumerate(dest_labels):
        out_trips = classified[i]["outbound"]
        ret_trips = classified[i]["return"]
        per_dest_timing[lbl] = {
            "morningHist": compute_departure_histogram(out_trips, bucket_minutes=15),
            "returnHist": compute_departure_histogram(ret_trips, bucket_minutes=15),
            "bestTimes": compute_best_times(out_trips, ret_trips, bucket_minutes=15),
            "durationTrends": compute_duration_trends_from_trips(out_trips, ret_trips),
        }

    data = {
        "kpis": kpis,
        "monthly": monthly,
        "destSplit": dest_split,
        "dayBreakdown": day_breakdown,
        "perDestTiming": per_dest_timing,
        "destLabels": dest_labels,
        "fuelTrend": fuel_trend,
        "evTrend": ev_trend,
        "missingDays": missing_days,
        "tripLog": trip_log,
        "currency": {"code": currency_code, "symbol": currency_symbol},
    }

    print("Building HTML...")
    html = build_commute_html(
        data, vehicle, currency_code, currency_symbol,
        origin, destinations, dest_labels, origin_label,
    )

    save_cache(cache)

    output_path = Path(__file__).parent / "commute_report.html"
    output_path.write_text(html)
    size_kb = output_path.stat().st_size / 1024
    print(f"  Saved to {output_path} ({size_kb:.0f} KB)")
    conn.close()


if __name__ == "__main__":
    main()
