"""Build a self-contained HTML dashboard from trips.db."""

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).parent / "trips.db"

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


def load_all_vehicles(conn: sqlite3.Connection) -> list[dict]:
    """Load all vehicles from DB. Returns list of dicts with 'vin', 'alias', 'brand'."""
    try:
        rows = conn.execute("SELECT vin, alias, brand FROM vehicles").fetchall()
        if rows:
            return [{"vin": r[0], "alias": r[1] or "My Car", "brand": r[2] or ""} for r in rows]
    except sqlite3.OperationalError:
        pass  # table doesn't exist yet
    return []


def load_trips(conn: sqlite3.Connection, vin: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trips WHERE vin = ? ORDER BY trip_start_time", (vin,)
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        warsaw = ZoneInfo("Europe/Warsaw")
        start = datetime.fromisoformat(d["trip_start_time"]).astimezone(warsaw) if d.get("trip_start_time") else None
        if not start:
            continue
        countries_raw = d.get("countries")
        result.append({
            "start": start,
            "end": datetime.fromisoformat(d["trip_end_time"]).astimezone(warsaw) if d.get("trip_end_time") else None,
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
            "score_accel": d.get("score_accel"),
            "score_brake": d.get("score_braking"),
            # New enriched fields
            "ev_duration_sec": d.get("ev_duration_sec") or 0,
            "eco_time_sec": d.get("eco_time_sec") or 0,
            "eco_distance_m": d.get("eco_distance_m") or 0,
            "power_time_sec": d.get("power_time_sec") or 0,
            "power_distance_m": d.get("power_distance_m") or 0,
            "charge_time_sec": d.get("charge_time_sec") or 0,
            "charge_distance_m": d.get("charge_distance_m") or 0,
            "max_speed_kmh": d.get("max_speed_kmh"),
            "avg_speed_kmh": d.get("avg_speed_kmh"),
            "highway_distance_m": d.get("highway_distance_m") or 0,
            "highway_duration_sec": d.get("highway_duration_sec") or 0,
            "idle_duration_sec": d.get("idle_duration_sec") or 0,
            "night_trip": d.get("night_trip"),
            "overspeed_distance_m": d.get("overspeed_distance_m") or 0,
            "overspeed_duration_sec": d.get("overspeed_duration_sec") or 0,
            "countries": json.loads(countries_raw) if countries_raw else [],
            "trip_category": d.get("trip_category"),
        })
    conn.row_factory = None
    return result


def load_enriched_waypoints(conn: sqlite3.Connection, vin: str) -> dict:
    """Load waypoints grouped by type for layered heatmap, filtered by VIN."""
    def _query(extra_where=""):
        base = (
            "SELECT ROUND(w.lat, 4) AS rlat, ROUND(w.lng, 4) AS rlng, COUNT(*) AS cnt "
            "FROM waypoints w JOIN trips t ON w.trip_start_time = t.trip_start_time "
            "WHERE t.vin = ?"
        )
        if extra_where:
            base += f" AND {extra_where}"
        base += " GROUP BY rlat, rlng"
        rows = conn.execute(base, (vin,)).fetchall()
        return [[r[0], r[1], math.log1p(r[2])] for r in rows]

    return {
        "all": _query(),
        "ev": _query("w.is_ev = 1"),
        "highway": _query("w.highway = 1"),
        "overspeed": _query("w.overspeed = 1"),
    }


def load_service_history(conn: sqlite3.Connection, vin: str) -> list[dict]:
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM service_history WHERE vin = ? ORDER BY service_date DESC", (vin,)
        ).fetchall()
        conn.row_factory = None
        return [
            {
                "date": r["service_date"],
                "category": r["service_category"],
                "provider": r["service_provider"],
                "odometer": r["odometer"],
                "operations": json.loads(r["operations_performed"]) if r["operations_performed"] else None,
                "notes": r["notes"],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def load_telemetry_history(conn: sqlite3.Connection, vin: str) -> list[dict]:
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM telemetry_snapshots WHERE vin = ? ORDER BY captured_at", (vin,)
        ).fetchall()
        conn.row_factory = None
        return [
            {
                "date": r["captured_at"][:10],
                "odometer": r["odometer"],
                "fuel_level": r["fuel_level"],
                "battery_level": r["battery_level"],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def compute_monthly(trips: list[dict]) -> dict:
    months: dict[str, dict] = defaultdict(lambda: {
        "trips": 0, "distance": 0, "ev_distance": 0, "fuel": 0, "duration": 0,
        "scores": [], "avg_speeds": [], "highway_km": 0,
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
        if t["avg_speed_kmh"] is not None and t["avg_speed_kmh"] > 0:
            m["avg_speeds"].append(t["avg_speed_kmh"])
        m["highway_km"] += t["highway_distance_m"] / 1000

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
        "avg_speed": [
            round(sum(months[k]["avg_speeds"]) / len(months[k]["avg_speeds"]), 1)
            if months[k]["avg_speeds"] else 0
            for k in labels
        ],
        "highway_km": [round(months[k]["highway_km"], 1) for k in labels],
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
    cats = {"Short (<10 km)": 0, "Medium (10-100)": 0, "Long (>100 km)": 0}
    for t in trips:
        d = t["distance_km"]
        if d < 10:
            cats["Short (<10 km)"] += 1
        elif d < 100:
            cats["Medium (10-100)"] += 1
        else:
            cats["Long (>100 km)"] += 1
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
            "max_speed": round(t["max_speed_kmh"], 1) if t["max_speed_kmh"] else None,
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
    avg_l100 = total_fuel / total_dist * 100 if total_dist > 0 else 0
    co2_saved = (total_ev * avg_l100 / 100) * CO2_KG_PER_LITER if total_dist > 0 else 0

    # New enriched KPIs
    avg_speeds = [t["avg_speed_kmh"] for t in trips if t["avg_speed_kmh"] is not None and t["avg_speed_kmh"] > 0]
    max_speeds = [t["max_speed_kmh"] for t in trips if t["max_speed_kmh"] is not None and t["max_speed_kmh"] > 0]
    highway_dist = sum(t["highway_distance_m"] / 1000 for t in trips)
    idle_sec = sum(t["idle_duration_sec"] for t in trips)
    night_count = sum(1 for t in trips if t["night_trip"] == 1)

    all_countries = set()
    for t in trips:
        if t["countries"]:
            all_countries.update(t["countries"])

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
        # New KPIs
        "avg_speed_kmh": round(sum(avg_speeds) / len(avg_speeds), 1) if avg_speeds else 0,
        "max_speed_ever": round(max(max_speeds), 1) if max_speeds else 0,
        "highway_pct": round(highway_dist / total_dist * 100, 1) if total_dist > 0 else 0,
        "idle_pct": round(idle_sec / total_dur * 100, 1) if total_dur > 0 else 0,
        "idle_hours": round(idle_sec / 3600, 1),
        "night_trip_count": night_count,
        "countries_visited": len(all_countries),
        "countries_list": sorted(all_countries),
    }


def compute_driving_modes(trips: list[dict]) -> dict:
    """Total EV/Eco/Power/Charge time and distance breakdown."""
    total_ev_time = sum(t["ev_duration_sec"] for t in trips)
    total_eco_time = sum(t["eco_time_sec"] for t in trips)
    total_power_time = sum(t["power_time_sec"] for t in trips)
    total_charge_time = sum(t["charge_time_sec"] for t in trips)

    total_ev_dist = sum(t["ev_distance_km"] for t in trips)
    total_eco_dist = sum(t["eco_distance_m"] / 1000 for t in trips)
    total_power_dist = sum(t["power_distance_m"] / 1000 for t in trips)
    total_charge_dist = sum(t["charge_distance_m"] / 1000 for t in trips)

    return {
        "time_labels": ["Electric", "Eco", "Power", "Charge"],
        "time_values": [
            round(total_ev_time / 3600, 1),
            round(total_eco_time / 3600, 1),
            round(total_power_time / 3600, 1),
            round(total_charge_time / 3600, 1),
        ],
        "dist_labels": ["Electric", "Eco", "Power", "Charge"],
        "dist_values": [
            round(total_ev_dist, 1),
            round(total_eco_dist, 1),
            round(total_power_dist, 1),
            round(total_charge_dist, 1),
        ],
    }


def compute_speed_analytics(trips: list[dict]) -> dict:
    """Max speed histogram + monthly avg/max speed trends."""
    buckets = defaultdict(int)
    monthly = defaultdict(lambda: {"speeds": [], "max_speeds": []})

    for t in trips:
        max_spd = t["max_speed_kmh"]
        avg_spd = t["avg_speed_kmh"]
        key = t["start"].strftime("%Y-%m")
        if max_spd is not None and max_spd > 0:
            bucket = (int(max_spd) // 10) * 10
            buckets[bucket] += 1
            monthly[key]["max_speeds"].append(max_spd)
        if avg_spd is not None and avg_spd > 0:
            monthly[key]["speeds"].append(avg_spd)

    if buckets:
        max_bucket = max(buckets.keys())
        hist_labels = list(range(0, max_bucket + 10, 10))
    else:
        hist_labels = list(range(0, 160, 10))

    labels = sorted(monthly.keys())
    return {
        "hist_labels": [f"{b}-{b+9}" for b in hist_labels],
        "hist_counts": [buckets.get(b, 0) for b in hist_labels],
        "monthly_labels": labels,
        "monthly_avg": [
            round(sum(monthly[k]["speeds"]) / len(monthly[k]["speeds"]), 1)
            if monthly[k]["speeds"] else 0
            for k in labels
        ],
        "monthly_max": [
            round(max(monthly[k]["max_speeds"]), 1)
            if monthly[k]["max_speeds"] else 0
            for k in labels
        ],
    }


def compute_highway_city_split(trips: list[dict]) -> dict:
    """Monthly highway vs city distance."""
    monthly = defaultdict(lambda: {"highway_km": 0, "total_km": 0})
    for t in trips:
        key = t["start"].strftime("%Y-%m")
        monthly[key]["highway_km"] += t["highway_distance_m"] / 1000
        monthly[key]["total_km"] += t["distance_km"]

    labels = sorted(monthly.keys())
    return {
        "labels": labels,
        "highway": [round(monthly[k]["highway_km"], 1) for k in labels],
        "city": [round(max(0, monthly[k]["total_km"] - monthly[k]["highway_km"]), 1) for k in labels],
    }


def compute_night_driving(trips: list[dict]) -> dict:
    """Night vs day trip stats."""
    night_trips = [t for t in trips if t["night_trip"] == 1]
    day_trips = [t for t in trips if t["night_trip"] == 0]

    night_fuel = sum(t["fuel_ml"] for t in night_trips)
    night_dist = sum(t["distance_km"] for t in night_trips)
    day_fuel = sum(t["fuel_ml"] for t in day_trips)
    day_dist = sum(t["distance_km"] for t in day_trips)

    return {
        "night_count": len(night_trips),
        "day_count": len(day_trips),
        "unknown_count": len(trips) - len(night_trips) - len(day_trips),
        "night_avg_fuel": round(night_fuel / night_dist * 100, 2) if night_dist > 0 else 0,
        "day_avg_fuel": round(day_fuel / day_dist * 100, 2) if day_dist > 0 else 0,
        "night_avg_dist": round(night_dist / len(night_trips), 1) if night_trips else 0,
        "day_avg_dist": round(day_dist / len(day_trips), 1) if day_trips else 0,
    }


def compute_idle_analysis(trips: list[dict]) -> dict:
    """Monthly idle time percentage and minutes."""
    monthly = defaultdict(lambda: {"idle_sec": 0, "total_sec": 0})
    for t in trips:
        key = t["start"].strftime("%Y-%m")
        monthly[key]["idle_sec"] += t["idle_duration_sec"]
        monthly[key]["total_sec"] += t["duration_sec"]

    labels = sorted(monthly.keys())
    return {
        "labels": labels,
        "idle_pct": [
            round(monthly[k]["idle_sec"] / monthly[k]["total_sec"] * 100, 1)
            if monthly[k]["total_sec"] > 0 else 0
            for k in labels
        ],
        "idle_min": [
            round(monthly[k]["idle_sec"] / 60, 1) for k in labels
        ],
    }


def build_html(kpis, monthly, weekday_hour, score_dist, heatmap_layers, longest_trips,
               trip_cats, seasonal, trips, driving_modes, speed_analytics,
               highway_city, night_driving, idle_trend, service_history, odometer_data,
               vehicle=None):
    vehicle = vehicle or {"alias": "My Car", "brand": ""}
    vehicle_name = vehicle["alias"]
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

    countries_str = ", ".join(kpis["countries_list"]) if kpis["countries_list"] else "N/A"

    data = {
        "kpis": kpis,
        "monthly": monthly,
        "weekdayHour": weekday_hour,
        "scoreDist": score_dist,
        "heatmapLayers": heatmap_layers,
        "center": [center_lat, center_lng],
        "longestTrips": longest_trips,
        "rollingFuel": rolling_fuel,
        "tripCats": trip_cats,
        "seasonal": seasonal,
        "drivingModes": driving_modes,
        "speedAnalytics": speed_analytics,
        "highwayCity": highway_city,
        "nightDriving": night_driving,
        "idleTrend": idle_trend,
        "serviceHistory": service_history,
        "odometerData": odometer_data,
    }

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{vehicle_name} Trip Dashboard</title>
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
<script>
(function(){{
  var d=localStorage.getItem('theme');
  if(d==='dark'||(d!=='light'&&window.matchMedia('(prefers-color-scheme:dark)').matches))
    document.documentElement.classList.add('dark');
}})();
</script>
<style>
  :root {{
    --bg-body: #f8fafc;
    --bg-card: #ffffff;
    --border-card: #e2e8f0;
    --text-heading: #1e293b;
    --text-body: #334155;
    --text-muted: #64748b;
    --text-faint: #94a3b8;
    --text-footer: #94a3b8;
    --chart-grid: rgba(0,0,0,0.06);
    --chart-tick: #64748b;
    --border-table: #e2e8f0;
    --bg-hover: #f1f5f9;
    --heat-inactive-bg: #e2e8f0;
    --heat-inactive-text: #334155;
  }}
  :root.dark {{
    --bg-body: #030712;
    --bg-card: rgba(31,41,55,0.5);
    --border-card: rgba(55,65,81,0.5);
    --text-heading: #ffffff;
    --text-body: #e5e7eb;
    --text-muted: #9ca3af;
    --text-faint: #6b7280;
    --text-footer: #4b5563;
    --chart-grid: rgba(255,255,255,0.06);
    --chart-tick: #9ca3af;
    --border-table: #1f2937;
    --bg-hover: rgba(31,41,55,0.5);
    --heat-inactive-bg: #374151;
    --heat-inactive-text: #ffffff;
  }}
  body {{
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    background: var(--bg-body);
    color: var(--text-body);
    transition: background-color 0.3s, color 0.2s;
  }}
  .card {{
    background: var(--bg-card);
    backdrop-filter: blur(12px);
    border-radius: 1rem;
    border: 1px solid var(--border-card);
    padding: 1.5rem;
    transition: background-color 0.3s, border-color 0.2s;
  }}
  #heatmap {{ height: 500px; border-radius: 1rem; z-index: 1; }}
  .kpi-value {{
    font-size: 1.875rem; line-height: 2.25rem;
    font-weight: 700;
    color: var(--text-heading);
  }}
  .kpi-label {{
    font-size: 0.875rem;
    color: var(--text-muted);
    margin-top: 0.25rem;
  }}
  .text-heading {{ color: var(--text-heading); }}
  .text-muted {{ color: var(--text-muted); }}
  .text-faint {{ color: var(--text-faint); }}
  .text-footer {{ color: var(--text-footer); }}
  .border-themed {{ border-color: var(--border-table); }}
  .row-hover:hover {{ background: var(--bg-hover); }}
  .heat-btn-inactive {{ background: var(--heat-inactive-bg); color: var(--heat-inactive-text); }}
</style>
</head>
<body class="min-h-screen">
<div class="max-w-7xl mx-auto px-4 py-8">

  <!-- Header -->
  <div class="flex items-center justify-between mb-8">
    <div>
      <h1 class="text-3xl font-bold text-heading tracking-tight">{vehicle_name}</h1>
      <p class="text-muted mt-1">Trip Analytics Dashboard &middot; Hybrid</p>
    </div>
    <div class="flex items-center gap-4">
      <button id="themeToggle" onclick="applyTheme(!isDarkMode())" class="p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors" title="Toggle theme">
        <svg id="sunIcon" class="w-5 h-5 text-heading hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
        </svg>
        <svg id="moonIcon" class="w-5 h-5 text-heading hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
        </svg>
      </button>
      <div class="text-right text-sm text-faint">
        <div>{kpis['first_trip']} &mdash; {kpis['last_trip']}</div>
        <div>{kpis['total_trips']} trips recorded</div>
      </div>
    </div>
  </div>

  <!-- KPI Cards Row 1 -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
    <div class="card">
      <div class="kpi-value">{kpis['total_trips']}</div>
      <div class="kpi-label">Total Trips</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_distance_km']:,.0f}<span class="text-lg text-muted"> km</span></div>
      <div class="kpi-label">Total Distance</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['avg_fuel_l100km']}<span class="text-lg text-muted"> L/100</span></div>
      <div class="kpi-label">Avg Fuel Consumption</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['ev_ratio_pct']}<span class="text-lg text-muted">%</span></div>
      <div class="kpi-label">Electric Driving</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['avg_score']}</div>
      <div class="kpi-label">Avg Driving Score</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_hours']:,.0f}<span class="text-lg text-muted"> h</span></div>
      <div class="kpi-label">Time Driving</div>
    </div>
  </div>

  <!-- KPI Cards Row 2 — Cost & Environment -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
    <div class="card">
      <div class="kpi-value">{kpis['total_cost_pln']:,.0f}<span class="text-lg text-muted"> PLN</span></div>
      <div class="kpi-label">Total Fuel Cost</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['cost_per_km']}<span class="text-lg text-muted"> PLN/km</span></div>
      <div class="kpi-label">Cost per km</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_fuel_l']:,.0f}<span class="text-lg text-muted"> L</span></div>
      <div class="kpi-label">Total Fuel Used</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_ev_km']:,.0f}<span class="text-lg text-muted"> km</span></div>
      <div class="kpi-label">EV Distance</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['co2_emitted_kg']:,.0f}<span class="text-lg text-muted"> kg</span></div>
      <div class="kpi-label">CO2 Emitted</div>
    </div>
    <div class="card">
      <div class="kpi-value" style="color:#22c55e">{kpis['co2_saved_kg']:,.0f}<span class="text-lg text-muted"> kg</span></div>
      <div class="kpi-label">CO2 Saved by EV</div>
    </div>
  </div>

  <!-- KPI Cards Row 3 — Speed, Highway, Night -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
    <div class="card">
      <div class="kpi-value">{kpis['avg_speed_kmh']}<span class="text-lg text-muted"> km/h</span></div>
      <div class="kpi-label">Avg Speed</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['max_speed_ever']}<span class="text-lg text-muted"> km/h</span></div>
      <div class="kpi-label">Max Speed</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['highway_pct']}<span class="text-lg text-muted">%</span></div>
      <div class="kpi-label">Highway Distance</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['idle_pct']}<span class="text-lg text-muted">%</span></div>
      <div class="kpi-label">Idle Time</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['night_trip_count']}</div>
      <div class="kpi-label">Night Trips</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['countries_visited']}</div>
      <div class="kpi-label">Countries <span class="text-xs text-faint">({countries_str})</span></div>
    </div>
  </div>

  <!-- Tab Navigation -->
  <div class="flex gap-2 mb-8 flex-wrap" id="tabBar">
    <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium bg-lexus-600 text-white transition-colors" data-tab="overview" onclick="switchTab('overview')">Overview</button>
    <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium heat-btn-inactive transition-colors" data-tab="fuel-ev" onclick="switchTab('fuel-ev')">Fuel &amp; EV</button>
    <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium heat-btn-inactive transition-colors" data-tab="driving" onclick="switchTab('driving')">Driving</button>
    <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium heat-btn-inactive transition-colors" data-tab="trips" onclick="switchTab('trips')">Trips</button>
  </div>

  <div id="tab-overview" data-tabpanel>
  <!-- Heatmap with Layer Toggle -->
  <div class="card mb-8 !p-0 overflow-hidden">
    <div class="p-6 pb-2 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
      <div>
        <h2 class="text-xl font-semibold text-heading">Route Heatmap</h2>
        <p class="text-sm text-muted">All {kpis['total_trips']} trips overlaid &middot; brighter = more frequent &middot; <span id="heatmapPts"></span> waypoints</p>
      </div>
      <div class="flex gap-2 flex-wrap">
        <button class="heat-btn px-3 py-1 rounded-full text-sm text-white bg-lexus-600 transition-colors" data-layer="all" onclick="switchHeatLayer('all')">All</button>
        <button class="heat-btn px-3 py-1 rounded-full text-sm heat-btn-inactive transition-colors" data-layer="ev" onclick="switchHeatLayer('ev')">EV</button>
        <button class="heat-btn px-3 py-1 rounded-full text-sm heat-btn-inactive transition-colors" data-layer="highway" onclick="switchHeatLayer('highway')">Highway</button>
        <button class="heat-btn px-3 py-1 rounded-full text-sm heat-btn-inactive transition-colors" data-layer="overspeed" onclick="switchHeatLayer('overspeed')">Over Limit</button>
      </div>
    </div>
    <div id="heatmap"></div>
  </div>

  <!-- Monthly Charts Row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Monthly Distance</h3>
      <canvas id="monthlyDistance"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Monthly Fuel Cost (PLN)</h3>
      <canvas id="monthlyCost"></canvas>
    </div>
  </div>

  <!-- Fuel Efficiency Trend -->
  <div class="card mb-8">
    <h3 class="text-lg font-semibold text-heading mb-4">Fuel Efficiency Trend (20-trip rolling avg, L/100km)</h3>
    <div style="height:200px"><canvas id="fuelTrend"></canvas></div>
  </div>
  </div><!-- /tab-overview -->

  <div id="tab-fuel-ev" data-tabpanel style="display:none">
  <!-- Fuel + EV Row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Monthly Fuel Consumption (L/100km)</h3>
      <canvas id="monthlyFuel"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Electric vs Fuel Distance by Month</h3>
      <canvas id="evIce"></canvas>
    </div>
  </div>

  <!-- Doughnut Charts Row -->
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">Drive Mode Time (h)</h3>
      <canvas id="modeTime"></canvas>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">Drive Mode Distance (km)</h3>
      <canvas id="modeDist"></canvas>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">Night vs Day</h3>
      <canvas id="nightDay"></canvas>
      <div class="grid grid-cols-2 gap-2 mt-3 text-xs">
        <div class="text-center">
          <div class="text-muted">Night</div>
          <div class="text-heading font-semibold" id="nightFuel"></div>
        </div>
        <div class="text-center">
          <div class="text-muted">Day</div>
          <div class="text-heading font-semibold" id="dayFuel"></div>
        </div>
      </div>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">Trip Categories</h3>
      <canvas id="tripCats"></canvas>
    </div>
  </div>

  <!-- Seasonal Charts -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">EV Ratio by Season</h3>
      <canvas id="seasonalEv"></canvas>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">Fuel by Season (L/100km)</h3>
      <canvas id="seasonalFuel"></canvas>
    </div>
  </div>
  </div><!-- /tab-fuel-ev -->

  <div id="tab-driving" data-tabpanel style="display:none">
  <!-- Speed Analytics -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Max Speed Distribution</h3>
      <canvas id="speedHist"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Monthly Speed Trends</h3>
      <canvas id="speedTrend"></canvas>
    </div>
  </div>

  <!-- Highway vs City -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Highway vs City (km/month)</h3>
      <canvas id="highwayCity"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Idle Time Trend (%)</h3>
      <canvas id="idleTrend"></canvas>
    </div>
  </div>

  <!-- Score Charts -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">Monthly Driving Score (Toyota app)</h3>
      <canvas id="monthlyScore"></canvas>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">Driving Score Distribution</h3>
      <canvas id="scoreDist"></canvas>
    </div>
  </div>

  <!-- Time Patterns Row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Trips by Day of Week</h3>
      <canvas id="weekday"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">Trips by Hour of Day</h3>
      <canvas id="hourly"></canvas>
    </div>
  </div>

  </div><!-- /tab-driving -->

  <div id="tab-trips" data-tabpanel style="display:none">
  <!-- Top Trips Table -->
  <div class="card mb-8 overflow-x-auto">
    <h3 class="text-lg font-semibold text-heading mb-4">Longest Trips</h3>
    <table class="w-full text-sm">
      <thead>
        <tr class="text-muted border-b border-themed">
          <th class="text-left py-2 px-3">Date</th>
          <th class="text-right py-2 px-3">Distance (km)</th>
          <th class="text-right py-2 px-3">Duration (min)</th>
          <th class="text-right py-2 px-3">Fuel (L)</th>
          <th class="text-right py-2 px-3">Cost (PLN)</th>
          <th class="text-right py-2 px-3">EV %</th>
          <th class="text-right py-2 px-3">Max km/h</th>
          <th class="text-right py-2 px-3">Score</th>
        </tr>
      </thead>
      <tbody id="topTripsBody"></tbody>
    </table>
  </div>

  <!-- Service History -->
  <div class="card mb-8 overflow-x-auto" id="serviceSection" style="display:none">
    <h3 class="text-lg font-semibold text-heading mb-4">Service History</h3>
    <table class="w-full text-sm">
      <thead>
        <tr class="text-muted border-b border-themed">
          <th class="text-left py-2 px-3">Date</th>
          <th class="text-left py-2 px-3">Category</th>
          <th class="text-left py-2 px-3">Provider</th>
          <th class="text-right py-2 px-3">Odometer (km)</th>
          <th class="text-left py-2 px-3">Notes</th>
        </tr>
      </thead>
      <tbody id="serviceBody"></tbody>
    </table>
  </div>

  <!-- Odometer Tracking -->
  <div class="card mb-8" id="odometerSection" style="display:none">
    <h3 class="text-lg font-semibold text-heading mb-4">Odometer Tracking</h3>
    <canvas id="odometerChart"></canvas>
  </div>
  </div><!-- /tab-trips -->

  <footer class="text-center text-xs text-footer py-8">
    Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} &middot; {vehicle_name} Trip Dashboard
    &middot; Fuel prices: unleaded 95 monthly avg (e-petrol.pl)
  </footer>
</div>

<script>
const D = {json.dumps(data, separators=(',', ':'))};

// --- Theme ---
function isDarkMode() {{ return document.documentElement.classList.contains('dark'); }}
function getThemeColors() {{
  const s = getComputedStyle(document.documentElement);
  return {{
    grid: s.getPropertyValue('--chart-grid').trim(),
    tick: s.getPropertyValue('--chart-tick').trim(),
    heading: s.getPropertyValue('--text-heading').trim(),
    muted: s.getPropertyValue('--text-muted').trim(),
  }};
}}
let gridColor = getThemeColors().grid;
let tickColor = getThemeColors().tick;
Chart.defaults.color = tickColor;
Chart.defaults.borderColor = gridColor;
Chart.defaults.plugins.legend.labels.boxWidth = 12;

const charts = [];
function createChart(id, config) {{
  const c = new Chart(document.getElementById(id), config);
  charts.push(c);
  return c;
}}

// --- Heatmap with Layer Toggle ---
const map = L.map('heatmap').setView(D.center, 11);
const tileUrls = {{
  dark: 'https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',
  light: 'https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png',
}};
let tileLayer = L.tileLayer(tileUrls[isDarkMode()?'dark':'light'], {{
  attribution: '&copy; OSM &amp; Carto',
  subdomains: 'abcd', maxZoom: 19
}}).addTo(map);

const heatGradients = {{
  all: {{0.0:'#0d1b2a', 0.15:'#1b3a5c', 0.3:'#1976d2', 0.5:'#26c6da', 0.7:'#ffa726', 0.85:'#ef5350', 1:'#ffe66d'}},
  ev: {{0.0:'#0d1b2a', 0.2:'#064e3b', 0.4:'#059669', 0.6:'#34d399', 0.8:'#6ee7b7', 1:'#d1fae5'}},
  highway: {{0.0:'#0d1b2a', 0.2:'#1e3a8a', 0.4:'#2563eb', 0.6:'#3b82f6', 0.8:'#60a5fa', 1:'#bfdbfe'}},
  overspeed: {{0.0:'#0d1b2a', 0.2:'#7f1d1d', 0.4:'#b91c1c', 0.6:'#dc2626', 0.8:'#f87171', 1:'#fecaca'}},
}};
const heatOpts = {{radius: 12, blur: 18, maxZoom: 17, minOpacity: 0.35}};
const heatLayers = {{}};
for (const [key, pts] of Object.entries(D.heatmapLayers)) {{
  if (pts && pts.length > 0) {{
    heatLayers[key] = L.heatLayer(pts, {{...heatOpts, gradient: heatGradients[key]}});
  }}
}}
if (heatLayers.all) heatLayers.all.addTo(map);
let activeHeatLayer = 'all';

function switchHeatLayer(name) {{
  if (heatLayers[activeHeatLayer]) map.removeLayer(heatLayers[activeHeatLayer]);
  if (heatLayers[name]) heatLayers[name].addTo(map);
  activeHeatLayer = name;
  document.querySelectorAll('.heat-btn').forEach(btn => {{
    const isActive = btn.dataset.layer === name;
    btn.classList.toggle('bg-lexus-600', isActive);
    btn.classList.toggle('text-white', isActive);
    btn.classList.toggle('heat-btn-inactive', !isActive);
  }});
  const pts = D.heatmapLayers[name] || [];
  document.getElementById('heatmapPts').textContent =
    pts.reduce((s,p) => s + Math.round(Math.expm1(p[2])), 0).toLocaleString();
}}
// Init count
switchHeatLayer('all');

// --- Monthly Distance ---
createChart('monthlyDistance', {{
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
createChart('monthlyCost', {{
  type: 'bar',
  data: {{
    labels: D.monthly.labels,
    datasets: [{{
      label: 'Fuel Cost (PLN)',
      data: D.monthly.fuel_cost_pln,
      backgroundColor: 'rgba(234,179,8,0.7)',
      borderRadius: 6,
    }}, {{
      label: 'Fuel Price (PLN/L)',
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
createChart('monthlyFuel', {{
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

// --- Electric vs Fuel ---
createChart('evIce', {{
  type: 'bar',
  data: {{
    labels: D.monthly.labels,
    datasets: [{{
      label: 'Electric (km)',
      data: D.monthly.ev_distance,
      backgroundColor: 'rgba(34,197,94,0.7)',
      borderRadius: 6,
    }}, {{
      label: 'Fuel (km)',
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

// --- Drive Mode Time (Doughnut) ---
createChart('modeTime', {{
  type: 'doughnut',
  data: {{
    labels: D.drivingModes.time_labels,
    datasets: [{{
      data: D.drivingModes.time_values,
      backgroundColor: ['#34d399','#60a5fa','#f97316','#a78bfa'],
      borderWidth: 0,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ padding: 8, font: {{ size: 11 }} }} }},
      tooltip: {{ callbacks: {{ label: ctx => `${{ctx.label}}: ${{ctx.parsed}} h` }} }}
    }}
  }}
}});

// --- Drive Mode Distance (Doughnut) ---
createChart('modeDist', {{
  type: 'doughnut',
  data: {{
    labels: D.drivingModes.dist_labels,
    datasets: [{{
      data: D.drivingModes.dist_values,
      backgroundColor: ['#34d399','#60a5fa','#f97316','#a78bfa'],
      borderWidth: 0,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ padding: 8, font: {{ size: 11 }} }} }},
      tooltip: {{ callbacks: {{ label: ctx => `${{ctx.label}}: ${{ctx.parsed}} km` }} }}
    }}
  }}
}});

// --- Speed Histogram ---
createChart('speedHist', {{
  type: 'bar',
  data: {{
    labels: D.speedAnalytics.hist_labels,
    datasets: [{{
      label: 'Trips',
      data: D.speedAnalytics.hist_counts,
      backgroundColor: 'rgba(14,165,233,0.7)',
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ title: {{ display: true, text: 'Trips' }}, grid: {{ color: gridColor }} }},
      x: {{ title: {{ display: true, text: 'Max Speed (km/h)' }}, grid: {{ display: false }} }}
    }}
  }}
}});

// --- Speed Trends ---
createChart('speedTrend', {{
  type: 'line',
  data: {{
    labels: D.speedAnalytics.monthly_labels,
    datasets: [{{
      label: 'Avg Speed (km/h)',
      data: D.speedAnalytics.monthly_avg,
      borderColor: '#0ea5e9',
      backgroundColor: 'rgba(14,165,233,0.1)',
      fill: true,
      tension: 0.3,
      pointRadius: 4,
    }}, {{
      label: 'Max Speed (km/h)',
      data: D.speedAnalytics.monthly_max,
      borderColor: '#ef4444',
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: 4,
      borderDash: [5,3],
    }}]
  }},
  options: {{
    responsive: true,
    interaction: {{ mode: 'index', intersect: false }},
    scales: {{
      y: {{ title: {{ display: true, text: 'km/h' }}, grid: {{ color: gridColor }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Highway vs City ---
createChart('highwayCity', {{
  type: 'bar',
  data: {{
    labels: D.highwayCity.labels,
    datasets: [{{
      label: 'Highway (km)',
      data: D.highwayCity.highway,
      backgroundColor: 'rgba(59,130,246,0.7)',
      borderRadius: 6,
    }}, {{
      label: 'City (km)',
      data: D.highwayCity.city,
      backgroundColor: 'rgba(234,179,8,0.5)',
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

// --- Idle Trend ---
createChart('idleTrend', {{
  type: 'line',
  data: {{
    labels: D.idleTrend.labels,
    datasets: [{{
      label: 'Idle %',
      data: D.idleTrend.idle_pct,
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
      y: {{ title: {{ display: true, text: '%' }}, grid: {{ color: gridColor }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Night vs Day (Pie) ---
createChart('nightDay', {{
  type: 'doughnut',
  data: {{
    labels: ['Night', 'Day'],
    datasets: [{{
      data: [D.nightDriving.night_count, D.nightDriving.day_count],
      backgroundColor: ['#1e3a5f','#fbbf24'],
      borderWidth: 0,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ padding: 8, font: {{ size: 11 }} }} }}
    }}
  }}
}});
document.getElementById('nightFuel').textContent = D.nightDriving.night_avg_fuel + ' L/100km';
document.getElementById('dayFuel').textContent = D.nightDriving.day_avg_fuel + ' L/100km';

// --- Trip Categories (Doughnut) ---
createChart('tripCats', {{
  type: 'doughnut',
  data: {{
    labels: D.tripCats.labels,
    datasets: [{{
      data: D.tripCats.counts,
      backgroundColor: ['#0ea5e9','#f59e0b','#ef4444'],
      borderWidth: 0,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ padding: 8, font: {{ size: 11 }} }} }}
    }}
  }}
}});

// --- Seasonal EV Ratio ---
createChart('seasonalEv', {{
  type: 'bar',
  data: {{
    labels: D.seasonal.labels,
    datasets: [{{
      label: 'Electric Ratio (%)',
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
createChart('seasonalFuel', {{
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
createChart('monthlyScore', {{
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
createChart('scoreDist', {{
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
createChart('weekday', {{
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
createChart('hourly', {{
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
createChart('fuelTrend', {{
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
    maintainAspectRatio: false,
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
  tr.className = 'border-b border-themed row-hover';
  tr.innerHTML = `
    <td class="py-2 px-3">${{t.date}}</td>
    <td class="text-right py-2 px-3 font-medium text-heading">${{t.distance}}</td>
    <td class="text-right py-2 px-3">${{t.duration_min}}</td>
    <td class="text-right py-2 px-3">${{t.fuel}}</td>
    <td class="text-right py-2 px-3">${{t.cost}} PLN</td>
    <td class="text-right py-2 px-3">${{t.ev_pct}}%</td>
    <td class="text-right py-2 px-3">${{t.max_speed ?? '—'}}</td>
    <td class="text-right py-2 px-3">${{t.score ?? '—'}}</td>`;
  tbody.appendChild(tr);
}});

// --- Service History Table ---
if (D.serviceHistory && D.serviceHistory.length > 0) {{
  document.getElementById('serviceSection').style.display = '';
  const sTbody = document.getElementById('serviceBody');
  D.serviceHistory.forEach(s => {{
    const tr = document.createElement('tr');
    tr.className = 'border-b border-themed row-hover';
    tr.innerHTML = `
      <td class="py-2 px-3">${{s.date || '—'}}</td>
      <td class="py-2 px-3">${{s.category || '—'}}</td>
      <td class="py-2 px-3">${{s.provider || '—'}}</td>
      <td class="text-right py-2 px-3">${{s.odometer ? s.odometer.toLocaleString() : '—'}}</td>
      <td class="py-2 px-3">${{s.notes || '—'}}</td>`;
    sTbody.appendChild(tr);
  }});
}}

// --- Odometer Tracking ---
if (D.odometerData && D.odometerData.length > 1) {{
  document.getElementById('odometerSection').style.display = '';
  createChart('odometerChart', {{
    type: 'line',
    data: {{
      labels: D.odometerData.map(d => d.date),
      datasets: [{{
        label: 'Odometer (km)',
        data: D.odometerData.map(d => d.odometer),
        borderColor: '#0ea5e9',
        backgroundColor: 'rgba(14,165,233,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 4,
      }}]
    }},
    options: {{
      responsive: true,
      scales: {{
        y: {{ title: {{ display: true, text: 'km' }}, grid: {{ color: gridColor }} }},
        x: {{ grid: {{ display: false }} }}
      }}
    }}
  }});
}}

// --- Tab Navigation ---
function switchTab(name) {{
  document.querySelectorAll('[data-tabpanel]').forEach(el => el.style.display = 'none');
  document.getElementById('tab-' + name).style.display = '';
  document.querySelectorAll('.tab-btn').forEach(btn => {{
    const isActive = btn.dataset.tab === name;
    btn.classList.toggle('bg-lexus-600', isActive);
    btn.classList.toggle('text-white', isActive);
    btn.classList.toggle('heat-btn-inactive', !isActive);
  }});
  charts.forEach(c => {{ if (c.canvas && c.canvas.offsetParent !== null) c.resize(); }});
  if (name === 'overview') setTimeout(() => map.invalidateSize(), 50);
  localStorage.setItem('activeTab', name);
}}

// --- Theme Toggle ---
function applyTheme(dark) {{
  document.documentElement.classList.toggle('dark', dark);
  localStorage.setItem('theme', dark ? 'dark' : 'light');

  const tc = getThemeColors();
  gridColor = tc.grid;
  tickColor = tc.tick;
  Chart.defaults.color = tickColor;
  Chart.defaults.borderColor = gridColor;

  charts.forEach(c => {{
    if (!c.options.scales) return;
    Object.values(c.options.scales).forEach(scale => {{
      if (scale.grid) scale.grid.color = gridColor;
      if (scale.ticks) scale.ticks.color = tickColor;
      if (scale.title) scale.title.color = tickColor;
    }});
    if (c.options.plugins && c.options.plugins.legend && c.options.plugins.legend.labels) {{
      c.options.plugins.legend.labels.color = tickColor;
    }}
    c.update('none');
  }});

  // Swap map tiles
  map.removeLayer(tileLayer);
  tileLayer = L.tileLayer(tileUrls[dark?'dark':'light'], {{
    attribution: '&copy; OSM &amp; Carto',
    subdomains: 'abcd', maxZoom: 19
  }}).addTo(map);

  // Toggle icons
  document.getElementById('sunIcon').classList.toggle('hidden', !dark);
  document.getElementById('moonIcon').classList.toggle('hidden', dark);
}}

// Init theme icons
(function() {{
  const dark = isDarkMode();
  document.getElementById('sunIcon').classList.toggle('hidden', !dark);
  document.getElementById('moonIcon').classList.toggle('hidden', dark);
}})();

// Init tab navigation
(function() {{
  const saved = localStorage.getItem('activeTab');
  const valid = ['overview','fuel-ev','driving','trips'];
  switchTab(valid.includes(saved) ? saved : 'overview');
}})();
</script>
</body>
</html>"""
    return html


def build_dashboard_for_vehicle(conn: sqlite3.Connection, vehicle: dict) -> Path:
    """Build a dashboard HTML for a single vehicle. Returns the output path."""
    vin = vehicle["vin"]
    alias = vehicle["alias"]
    brand = vehicle["brand"]
    label = f"{alias} ({brand})" if brand else alias
    print(f"\n{'='*60}")
    print(f"Building dashboard for: {label}")
    print(f"{'='*60}")

    print("Loading trips...")
    trips = load_trips(conn, vin)
    print(f"  {len(trips)} trips")

    if not trips:
        print(f"  Skipping {alias} — no trips.")
        return None

    print("Loading heatmap waypoints...")
    heatmap_layers = load_enriched_waypoints(conn, vin)
    print(f"  {len(heatmap_layers['all'])} grid cells (all), {len(heatmap_layers['ev'])} (EV), "
          f"{len(heatmap_layers['highway'])} (highway), {len(heatmap_layers['overspeed'])} (overspeed)")

    print("Loading service history & telemetry...")
    service_history = load_service_history(conn, vin)
    odometer_data = load_telemetry_history(conn, vin)
    print(f"  {len(service_history)} service records, {len(odometer_data)} telemetry snapshots")

    print("Computing aggregations...")
    kpis = compute_kpis(trips)
    monthly = compute_monthly(trips)
    wh = compute_weekday_hour(trips)
    sd = compute_score_distribution(trips)
    lt = top_trips(trips)
    tc = compute_trip_categories(trips)
    sea = compute_seasonal(trips)
    dm = compute_driving_modes(trips)
    sa = compute_speed_analytics(trips)
    hc = compute_highway_city_split(trips)
    nd = compute_night_driving(trips)
    idle = compute_idle_analysis(trips)

    print("Building HTML...")
    html = build_html(kpis, monthly, wh, sd, heatmap_layers, lt, tc, sea, trips,
                      dm, sa, hc, nd, idle, service_history, odometer_data, vehicle)

    # Sanitize alias for filename
    safe_alias = "".join(c if c.isalnum() or c in "-_ " else "" for c in alias).strip().replace(" ", "_")
    output_path = Path(__file__).parent / f"dashboard_{safe_alias}.html"
    output_path.write_text(html)
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Saved to {output_path} ({size_mb:.1f} MB)")
    return output_path


def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        print("Run backfill.py first.")
        raise SystemExit(1)

    conn = sqlite3.connect(DB_PATH)

    vehicles = load_all_vehicles(conn)
    if not vehicles:
        # Fall back to distinct VINs from trips table
        vins = conn.execute("SELECT DISTINCT vin FROM trips").fetchall()
        vehicles = [{"vin": r[0], "alias": r[0][:8], "brand": ""} for r in vins if r[0]]
    if not vehicles:
        print("No vehicles found in database. Run backfill.py first.")
        raise SystemExit(1)

    print(f"Found {len(vehicles)} vehicle(s):")
    for v in vehicles:
        label = f"{v['alias']} ({v['brand']})" if v['brand'] else v['alias']
        print(f"  - {label}")

    outputs = []
    for vehicle in vehicles:
        path = build_dashboard_for_vehicle(conn, vehicle)
        if path:
            outputs.append(path)

    conn.close()

    if outputs:
        print(f"\n{'='*60}")
        print(f"Generated {len(outputs)} dashboard(s):")
        for p in outputs:
            print(f"  - {p}")
        print("Open them in your browser!")
    else:
        print("\nNo dashboards generated (no trips found).")


if __name__ == "__main__":
    main()
