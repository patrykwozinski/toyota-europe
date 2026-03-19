"""Build a self-contained HTML dashboard from trips.db."""

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fuel_config import (
    get_country_info,
    get_exchange_rate,
    get_fuel_price,
    load_cache,
    save_cache,
    COUNTRY_INFO,
)
from translations import get_translations

DB_PATH = Path(__file__).parent / "trips.db"

# CO2 emission factor for gasoline: 2.31 kg CO2 per liter
CO2_KG_PER_LITER = 2.31


def load_all_vehicles(conn: sqlite3.Connection) -> list[dict]:
    """Load all vehicles from DB. Returns list of dicts with 'vin', 'alias', 'brand', 'fuel_type', 'engine_type'."""
    try:
        rows = conn.execute("SELECT vin, alias, brand, fuel_type, engine_type FROM vehicles").fetchall()
        if rows:
            return [{"vin": r[0], "alias": r[1] or "My Car", "brand": r[2] or "", "fuel_type": r[3] or "gasoline", "engine_type": r[4]} for r in rows]
    except sqlite3.OperationalError:
        try:
            rows = conn.execute("SELECT vin, alias, brand, fuel_type FROM vehicles").fetchall()
            if rows:
                return [{"vin": r[0], "alias": r[1] or "My Car", "brand": r[2] or "", "fuel_type": r[3] or "gasoline", "engine_type": None} for r in rows]
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
            "score_constant": d.get("score_constant"),
            "score_advice": d.get("score_advice"),
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


def compute_monthly(trips: list[dict], price_fn=None) -> dict:
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
        "fuel_cost": [
            round(months[k]["fuel"] * price_fn(k), 2)
            for k in labels
        ],
        "fuel_price": [price_fn(k) for k in labels],
        "avg_speed": [
            round(sum(months[k]["avg_speeds"]) / len(months[k]["avg_speeds"]), 1)
            if months[k]["avg_speeds"] else 0
            for k in labels
        ],
        "highway_km": [round(months[k]["highway_km"], 1) for k in labels],
    }


def compute_weekday_hour(trips: list[dict], t: dict | None = None) -> dict:
    weekday_counts = [0] * 7
    weekday_dist = [0.0] * 7
    hour_counts = [0] * 24
    hour_dist = [0.0] * 24
    for tr in trips:
        wd = tr["start"].weekday()
        h = tr["start"].hour
        weekday_counts[wd] += 1
        weekday_dist[wd] += tr["distance_km"]
        hour_counts[h] += 1
        hour_dist[h] += tr["distance_km"]
    weekday_labels = t["weekdays"] if t else ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "weekday_labels": weekday_labels,
        "weekday_trips": weekday_counts,
        "weekday_distance": [round(d, 1) for d in weekday_dist],
        "hour_labels": [f"{hh:02d}" for hh in range(24)],
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


def compute_trip_categories(trips: list[dict], t: dict | None = None) -> dict:
    short_label = t["cat_short"] if t else "Short (<10 km)"
    medium_label = t["cat_medium"] if t else "Medium (10-100)"
    long_label = t["cat_long"] if t else "Long (>100 km)"
    cats = {short_label: 0, medium_label: 0, long_label: 0}
    for tr in trips:
        d = tr["distance_km"]
        if d < 10:
            cats[short_label] += 1
        elif d < 100:
            cats[medium_label] += 1
        else:
            cats[long_label] += 1
    return {"labels": list(cats.keys()), "counts": list(cats.values())}


def compute_seasonal(trips: list[dict], t: dict | None = None) -> dict:
    s_winter = t["season_winter"] if t else "Winter"
    s_spring = t["season_spring"] if t else "Spring"
    s_summer = t["season_summer"] if t else "Summer"
    s_autumn = t["season_autumn"] if t else "Autumn"
    seasons = {s_winter: {"ev": 0, "total": 0, "fuel": 0, "dist": 0},
               s_spring: {"ev": 0, "total": 0, "fuel": 0, "dist": 0},
               s_summer: {"ev": 0, "total": 0, "fuel": 0, "dist": 0},
               s_autumn: {"ev": 0, "total": 0, "fuel": 0, "dist": 0}}
    for tr in trips:
        m = tr["start"].month
        if m in (12, 1, 2):
            s = s_winter
        elif m in (3, 4, 5):
            s = s_spring
        elif m in (6, 7, 8):
            s = s_summer
        else:
            s = s_autumn
        seasons[s]["ev"] += tr["ev_distance_km"]
        seasons[s]["total"] += tr["distance_km"]
        seasons[s]["fuel"] += tr["fuel_ml"]
        seasons[s]["dist"] += tr["distance_km"]
    labels = [s_winter, s_spring, s_summer, s_autumn]
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


def stitch_journeys(trips: list[dict], max_gap_min: int = 45) -> list[list[dict]]:
    """Group consecutive trips with gap ≤ max_gap_min into journey legs."""
    from datetime import timedelta
    if not trips:
        return []
    max_gap = timedelta(minutes=max_gap_min)
    journeys: list[list[dict]] = []
    cur: list[dict] = [trips[0]]
    for t in trips[1:]:
        if cur[-1]["end"] is not None:
            gap = t["start"] - cur[-1]["end"]
            if timedelta(0) <= gap <= max_gap:
                cur.append(t)
                continue
        journeys.append(cur)
        cur = [t]
    journeys.append(cur)
    return journeys


def top_journeys(trips: list[dict], n: int = 20, max_gap_min: int = 45, price_fn=None) -> list[dict]:
    """Return top N longest journeys, stitching legs separated by ≤ max_gap_min minute breaks."""
    journeys = stitch_journeys(trips, max_gap_min)
    result = []
    for legs in journeys:
        total_dist = sum(t["distance_km"] for t in legs)
        driving_sec = sum(t["duration_sec"] for t in legs)
        if legs[-1]["end"] and legs[0]["start"]:
            total_sec = (legs[-1]["end"] - legs[0]["start"]).total_seconds()
        else:
            total_sec = driving_sec
        total_fuel = sum(t["fuel_ml"] for t in legs)
        avg_fuel = round(total_fuel / total_dist * 100, 2) if total_dist > 0 else 0
        max_speed = max((t["max_speed_kmh"] for t in legs if t.get("max_speed_kmh")), default=None)
        break_mins = []
        for i in range(1, len(legs)):
            if legs[i - 1]["end"]:
                gap_sec = (legs[i]["start"] - legs[i - 1]["end"]).total_seconds()
                if gap_sec > 0:
                    break_mins.append(round(gap_sec / 60, 1))
        cost = 0.0
        if price_fn:
            for t in legs:
                month = t["start"].strftime("%Y-%m")
                cost += t["fuel_ml"] * price_fn(month)
        result.append({
            "date": legs[0]["start"].strftime("%Y-%m-%d %H:%M"),
            "distance": round(total_dist, 1),
            "driving_min": round(driving_sec / 60, 1),
            "total_min": round(total_sec / 60, 1),
            "legs": len(legs),
            "breaks": break_mins,
            "fuel": round(total_fuel, 2),
            "avg_fuel": avg_fuel,
            "cost": round(cost, 2),
            "max_speed": round(max_speed, 1) if max_speed else None,
        })
    result.sort(key=lambda j: j["distance"], reverse=True)
    return result[:n]


def compute_kpis(trips: list[dict], price_fn=None) -> dict:
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
        total_cost += t["fuel_ml"] * price_fn(month)

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
        "total_cost": round(total_cost, 0),
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


def compute_driving_modes(trips: list[dict], t: dict | None = None) -> dict:
    """Total EV/Eco/Power/Charge time and distance breakdown."""
    total_ev_time = sum(tr["ev_duration_sec"] for tr in trips)
    total_eco_time = sum(tr["eco_time_sec"] for tr in trips)
    total_power_time = sum(tr["power_time_sec"] for tr in trips)
    total_charge_time = sum(tr["charge_time_sec"] for tr in trips)

    total_ev_dist = sum(tr["ev_distance_km"] for tr in trips)
    total_eco_dist = sum(tr["eco_distance_m"] / 1000 for tr in trips)
    total_power_dist = sum(tr["power_distance_m"] / 1000 for tr in trips)
    total_charge_dist = sum(tr["charge_distance_m"] / 1000 for tr in trips)

    mode_labels = [
        t["mode_electric"] if t else "Electric",
        t["mode_eco"] if t else "Eco",
        t["mode_power"] if t else "Power",
        t["mode_charge"] if t else "Charge",
    ]
    return {
        "time_labels": mode_labels,
        "time_values": [
            round(total_ev_time / 3600, 1),
            round(total_eco_time / 3600, 1),
            round(total_power_time / 3600, 1),
            round(total_charge_time / 3600, 1),
        ],
        "dist_labels": mode_labels,
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


def compute_driving_profile(trips: list[dict], t: dict | None = None) -> dict:
    """Compute driving style profile with radar axes, classification, and habits."""
    if not trips:
        return {
            "radar": {"labels": [], "values": []},
            "classification": {"label": t["class_unknown"] if t else "Unknown",
                               "description": t["class_unknown_desc"] if t else "Not enough data."},
            "speedProfile": {"labels": [], "counts": []},
            "roadType": {"highway_pct": 0, "city_pct": 100},
            "tripDistribution": {"labels": [], "counts": []},
            "habits": {"night_pct": 0, "weekend_pct": 0, "trips_per_day": 0, "peak_hour": "N/A"},
        }

    # --- Radar axes (0-100) ---
    # Smoothness: avg of score_accel + score_brake per trip
    smoothness_vals = []
    for tr in trips:
        sa = tr.get("score_accel")
        sb = tr.get("score_brake")
        if sa is not None and sb is not None:
            smoothness_vals.append((sa + sb) / 2)
    smoothness = sum(smoothness_vals) / len(smoothness_vals) if smoothness_vals else 50

    # Eco-Consciousness: eco mode time ratio + EV distance ratio
    total_dur = sum(tr["duration_sec"] for tr in trips)
    total_dist = sum(tr["distance_km"] for tr in trips)
    eco_time = sum(tr["eco_time_sec"] for tr in trips)
    ev_dist = sum(tr["ev_distance_km"] for tr in trips)
    eco_time_ratio = (eco_time / total_dur * 100) if total_dur > 0 else 0
    ev_dist_ratio = (ev_dist / total_dist * 100) if total_dist > 0 else 0
    eco_consciousness = min(100, (eco_time_ratio + ev_dist_ratio) / 2)

    # Speed Discipline: inverse of overspeed distance ratio
    overspeed_dist = sum(tr["overspeed_distance_m"] for tr in trips)
    overspeed_ratio = (overspeed_dist / (total_dist * 1000) * 100) if total_dist > 0 else 0
    speed_discipline = max(0, min(100, 100 - overspeed_ratio * 5))

    # Consistency: avg of score_constant
    const_vals = [tr["score_constant"] for tr in trips if tr.get("score_constant") is not None]
    consistency = sum(const_vals) / len(const_vals) if const_vals else 50

    # Calmness: inverse of power mode ratio + idle ratio
    power_time = sum(tr["power_time_sec"] for tr in trips)
    idle_time = sum(tr["idle_duration_sec"] for tr in trips)
    power_ratio = (power_time / total_dur * 100) if total_dur > 0 else 0
    idle_ratio = (idle_time / total_dur * 100) if total_dur > 0 else 0
    calmness = max(0, min(100, 100 - (power_ratio * 3 + idle_ratio)))

    radar_values = [
        round(smoothness, 1),
        round(eco_consciousness, 1),
        round(speed_discipline, 1),
        round(consistency, 1),
        round(calmness, 1),
    ]

    # --- Classification ---
    highway_dist = sum(tr["highway_distance_m"] / 1000 for tr in trips)
    highway_pct = (highway_dist / total_dist * 100) if total_dist > 0 else 0
    avg_speeds = [tr["avg_speed_kmh"] for tr in trips if tr.get("avg_speed_kmh") and tr["avg_speed_kmh"] > 0]
    avg_speed = sum(avg_speeds) / len(avg_speeds) if avg_speeds else 0
    avg_trip_km = total_dist / len(trips) if trips else 0

    if eco_consciousness >= 70 and smoothness >= 70 and speed_discipline >= 80:
        classification = {
            "label": t["class_eco_expert"] if t else "Eco Expert",
            "description": t["class_eco_expert_desc"] if t else "You maximize electric driving, maintain smooth inputs, and respect speed limits. Your driving style prioritizes efficiency above all."
        }
    elif calmness < 40 or speed_discipline < 50:
        classification = {
            "label": t["class_spirited"] if t else "Spirited Driver",
            "description": t["class_spirited_desc"] if t else "You enjoy dynamic driving with frequent use of power mode and higher speeds. You prioritize engagement over efficiency."
        }
    elif highway_pct > 50 and avg_speed > 60:
        classification = {
            "label": t["class_highway_warrior"] if t else "Highway Warrior",
            "description": t["class_highway_warrior_desc"] if t else "Most of your driving happens on highways at higher speeds. You cover long distances efficiently on motorways."
        }
    elif highway_pct < 20 and avg_trip_km < 15:
        classification = {
            "label": t["class_city_navigator"] if t else "City Navigator",
            "description": t["class_city_navigator_desc"] if t else "Your trips are predominantly urban and short. You navigate city traffic frequently, ideal for electric and hybrid powertrains."
        }
    elif smoothness >= 75 and consistency >= 70 and calmness >= 65:
        classification = {
            "label": t["class_smooth_cruiser"] if t else "Smooth Cruiser",
            "description": t["class_smooth_cruiser_desc"] if t else "You drive with consistent, smooth inputs and maintain a calm driving style. Your predictable driving is easy on passengers and the car."
        }
    else:
        classification = {
            "label": t["class_balanced"] if t else "Balanced Driver",
            "description": t["class_balanced_desc"] if t else "You have a well-rounded driving style that adapts to different conditions. A mix of city and highway driving with moderate efficiency."
        }

    # --- Speed profile histogram (6 buckets) ---
    speed_buckets = {"0-30": 0, "30-50": 0, "50-70": 0, "70-90": 0, "90-110": 0, "110+": 0}
    for tr in trips:
        spd = tr.get("avg_speed_kmh")
        if spd is None or spd <= 0:
            continue
        if spd < 30:
            speed_buckets["0-30"] += 1
        elif spd < 50:
            speed_buckets["30-50"] += 1
        elif spd < 70:
            speed_buckets["50-70"] += 1
        elif spd < 90:
            speed_buckets["70-90"] += 1
        elif spd < 110:
            speed_buckets["90-110"] += 1
        else:
            speed_buckets["110+"] += 1

    # --- Road type split ---
    city_pct = max(0, 100 - highway_pct)

    # --- Trip distance distribution (6 buckets) ---
    dist_buckets = {"0-5 km": 0, "5-15 km": 0, "15-30 km": 0, "30-60 km": 0, "60-100 km": 0, "100+ km": 0}
    for tr in trips:
        d = tr["distance_km"]
        if d < 5:
            dist_buckets["0-5 km"] += 1
        elif d < 15:
            dist_buckets["5-15 km"] += 1
        elif d < 30:
            dist_buckets["15-30 km"] += 1
        elif d < 60:
            dist_buckets["30-60 km"] += 1
        elif d < 100:
            dist_buckets["60-100 km"] += 1
        else:
            dist_buckets["100+ km"] += 1

    # --- Driving habits ---
    night_count = sum(1 for tr in trips if tr.get("night_trip") == 1)
    weekend_count = sum(1 for tr in trips if tr["start"].weekday() >= 5)
    night_pct = round(night_count / len(trips) * 100, 1) if trips else 0
    weekend_pct = round(weekend_count / len(trips) * 100, 1) if trips else 0

    if trips:
        first_day = trips[0]["start"].date()
        last_day = trips[-1]["start"].date()
        day_span = max(1, (last_day - first_day).days)
        trips_per_day = round(len(trips) / day_span, 1)
    else:
        trips_per_day = 0

    hour_counts = [0] * 24
    for tr in trips:
        hour_counts[tr["start"].hour] += 1
    peak_hour_idx = hour_counts.index(max(hour_counts))
    peak_hour = f"{peak_hour_idx:02d}:00"

    radar_labels = [
        t["radar_smoothness"] if t else "Smoothness",
        t["radar_eco"] if t else "Eco-Consciousness",
        t["radar_speed_discipline"] if t else "Speed Discipline",
        t["radar_consistency"] if t else "Consistency",
        t["radar_calmness"] if t else "Calmness",
    ]

    return {
        "radar": {
            "labels": radar_labels,
            "values": radar_values,
        },
        "classification": classification,
        "speedProfile": {
            "labels": list(speed_buckets.keys()),
            "counts": list(speed_buckets.values()),
        },
        "roadType": {
            "highway_pct": round(highway_pct, 1),
            "city_pct": round(city_pct, 1),
        },
        "tripDistribution": {
            "labels": list(dist_buckets.keys()),
            "counts": list(dist_buckets.values()),
        },
        "habits": {
            "night_pct": night_pct,
            "weekend_pct": weekend_pct,
            "trips_per_day": trips_per_day,
            "peak_hour": peak_hour,
        },
    }


def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """Linear interpolation with clamping. Maps x in [x0,x1] to [y0,y1]."""
    if x1 == x0:
        return (y0 + y1) / 2
    t = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
    return y0 + t * (y1 - y0)


def _sigmoid_score(x: float, center: float, steepness: float, low: float, high: float) -> float:
    """Smooth S-curve scoring for soft thresholds."""
    z = steepness * (x - center)
    z = max(-500, min(500, z))
    sig = 1 / (1 + math.exp(-z))
    return low + sig * (high - low)


def _estimate_savings(kpis, monthly, price_fn, currency_symbol, current_engine="Petrol"):
    """Estimate monthly/annual fuel cost projections per engine type.

    All costs are computed relative to the user's *actual* monthly spend, which
    belongs to ``current_engine``.  Other engines are scaled from that baseline
    so the user sees realistic comparisons (e.g. "switching from HEV to Petrol
    would cost 39% more").
    """
    if not monthly or not monthly.get("fuel_cost") or not kpis:
        return None
    costs = monthly["fuel_cost"]
    dists = monthly["distance"]
    n = min(6, len(costs))
    if n == 0:
        return None
    recent_costs = costs[-n:]
    recent_dists = dists[-n:]
    avg_monthly_cost = sum(recent_costs) / n
    avg_monthly_dist = sum(recent_dists) / n
    if avg_monthly_cost <= 0 or avg_monthly_dist <= 0:
        return None
    current_cost_per_km = avg_monthly_cost / avg_monthly_dist
    # Relative efficiency multipliers vs a hypothetical pure-petrol baseline.
    # avg_monthly_cost already reflects the current engine's efficiency, so we
    # first "undo" it to get a petrol-equivalent cost, then re-apply each
    # engine's multiplier.
    elec_ratio = 0.20  # BEV ~80% cheaper per km than petrol
    petrol_mult = {
        "BEV": elec_ratio,
        "PHEV": 0.45 + 0.55 * elec_ratio,  # ~0.56
        "HEV": 0.72,
        "Petrol": 1.0,
        "Diesel": 0.82,
    }
    # The user's actual cost *is* the cost at current_engine's multiplier.
    current_mult = petrol_mult.get(current_engine, 1.0)
    # Petrol-equivalent monthly cost (what the user would pay on pure petrol)
    petrol_monthly = avg_monthly_cost / current_mult if current_mult > 0 else avg_monthly_cost
    by_engine = {}
    for eng, pmult in petrol_mult.items():
        mc = round(petrol_monthly * pmult, 2)
        saving = round(avg_monthly_cost - mc, 2)
        pct = round((1 - mc / avg_monthly_cost) * 100, 1) if avg_monthly_cost > 0 else 0.0
        by_engine[eng] = {
            "monthly_cost": mc,
            "annual_cost": round(mc * 12, 2),
            "monthly_saving": saving,
            "annual_saving": round(saving * 12, 2),
            "pct_saving": pct,
            "is_current": eng == current_engine,
        }
    return {
        "current_monthly_cost": round(avg_monthly_cost, 2),
        "currency_symbol": currency_symbol,
        "current_engine": current_engine,
        "by_engine": by_engine,
    }


def compute_engine_recommendation(trips: list[dict], profile: dict, t: dict | None = None,
                                   fuel_type: str = "gasoline", engine_type: str | None = None,
                                   seasonal=None, night_driving=None, kpis=None,
                                   monthly=None, journeys=None, idle=None,
                                   price_fn=None, currency_symbol="zl") -> dict:
    """Score 5 engine types using 16 continuous factors and recommend the best fit."""
    if not trips:
        return {"scores": [], "recommendation": None, "runner_up": None, "current_engine": None}

    # --- Aggregate trip data ---
    total_dist = sum(tr["distance_km"] for tr in trips)
    total_dur = sum(tr["duration_sec"] for tr in trips)
    ev_dist = sum(tr["ev_distance_km"] for tr in trips)
    highway_dist = sum(tr["highway_distance_m"] / 1000 for tr in trips)
    total_fuel = sum(tr["fuel_ml"] for tr in trips)

    highway_pct = (highway_dist / total_dist * 100) if total_dist > 0 else 0
    city_pct = 100 - highway_pct
    ev_pct = (ev_dist / total_dist * 100) if total_dist > 0 else 0
    avg_trip_km = total_dist / len(trips) if trips else 0
    avg_fuel = (total_fuel / total_dist * 100) if total_dist > 0 else 0

    short_trips = sum(1 for tr in trips if tr["distance_km"] < 15)
    short_trip_pct = (short_trips / len(trips) * 100) if trips else 0

    radar = profile.get("radar", {}).get("values", [50, 50, 50, 50, 50])
    smoothness = radar[0] if len(radar) > 0 else 50
    eco_score = radar[1] if len(radar) > 1 else 50
    speed_discipline = radar[2] if len(radar) > 2 else 50
    consistency = radar[3] if len(radar) > 3 else 50
    calmness = radar[4] if len(radar) > 4 else 50

    habits = profile.get("habits", {})
    trips_per_day = habits.get("trips_per_day", 0)
    weekend_pct = habits.get("weekend_pct", 0)
    peak_hour_str = habits.get("peak_hour", "12:00")
    try:
        peak_hour = int(str(peak_hour_str).split(":")[0])
    except (ValueError, AttributeError, IndexError):
        peak_hour = 12

    driver_class = profile.get("classification", {}).get("label", "")
    max_trip_km = max((tr["distance_km"] for tr in trips), default=0)

    engines = {
        "BEV": {"label": t["engine_bev"] if t else "Battery Electric", "icon": "battery-full"},
        "PHEV": {"label": t["engine_phev"] if t else "Plug-in Hybrid", "icon": "plug"},
        "HEV": {"label": t["engine_hev"] if t else "Hybrid", "icon": "leaf"},
        "Petrol": {"label": t["engine_petrol"] if t else "Petrol", "icon": "gas-pump"},
        "Diesel": {"label": t["engine_diesel"] if t else "Diesel", "icon": "oil-can"},
    }
    ENGINE_KEYS = list(engines.keys())

    # ==================================================================
    # 16 CONTINUOUS FACTORS — each produces a 0-100 score per engine
    # ==================================================================
    factor_names = [
        "trip_distance", "highway_city", "ev_readiness", "driving_style",
        "short_trip_density", "fuel_efficiency", "speed_behavior", "driver_archetype",
        "seasonal_resilience", "idle_impact", "journey_feasibility", "regen_braking",
        "charging_alignment", "power_aggressiveness", "cost_sensitivity", "usage_pattern",
    ]
    factor_label_keys = [f"factor_{n}" for n in factor_names]
    factor_labels = [
        (t.get(k, k.replace("factor_", "").replace("_", " ").title()) if t
         else k.replace("factor_", "").replace("_", " ").title())
        for k in factor_label_keys
    ]

    weights = [0.12, 0.10, 0.08, 0.08, 0.06, 0.06, 0.05, 0.05,
               0.06, 0.05, 0.08, 0.05, 0.03, 0.04, 0.05, 0.04]

    NEUTRAL = {"BEV": 65, "PHEV": 65, "HEV": 65, "Petrol": 65, "Diesel": 65}

    # F1: Trip Distance Fit — BEV excels short, Diesel excels long, PHEV/HEV peak mid
    phev_f1 = (_lerp(avg_trip_km, 5, 40, 78, 92) if avg_trip_km <= 40
               else _lerp(avg_trip_km, 40, 150, 92, 55))
    hev_f1 = (_lerp(avg_trip_km, 5, 45, 72, 88) if avg_trip_km <= 45
              else _lerp(avg_trip_km, 45, 150, 88, 52))
    f1 = {
        "BEV":    _lerp(avg_trip_km, 10, 150, 95, 28),
        "PHEV":   phev_f1,
        "HEV":    hev_f1,
        "Petrol": _lerp(avg_trip_km, 10, 120, 45, 82),
        "Diesel": _lerp(avg_trip_km, 15, 120, 25, 92),
    }

    # F2: Highway vs City Balance
    f2 = {
        "BEV":    _sigmoid_score(city_pct, 50, 0.08, 38, 95),
        "PHEV":   _sigmoid_score(city_pct, 40, 0.06, 52, 90),
        "HEV":    _sigmoid_score(city_pct, 45, 0.07, 48, 92),
        "Petrol": _sigmoid_score(city_pct, 55, -0.05, 48, 82),
        "Diesel": _sigmoid_score(city_pct, 45, -0.07, 28, 90),
    }

    # F3: EV Readiness (current EV usage appetite)
    f3 = {
        "BEV":    _sigmoid_score(ev_pct, 15, 0.12, 35, 95),
        "PHEV":   _sigmoid_score(ev_pct, 10, 0.10, 48, 92),
        "HEV":    _sigmoid_score(ev_pct, 5, 0.08, 58, 85),
        "Petrol": _sigmoid_score(ev_pct, 20, -0.08, 20, 72),
        "Diesel": _sigmoid_score(ev_pct, 15, -0.10, 15, 65),
    }

    # F4: Driving Style Harmony (smooth + eco + calm composite)
    style_composite = 0.4 * smoothness + 0.35 * eco_score + 0.25 * calmness
    f4 = {
        "BEV":    _lerp(style_composite, 30, 85, 30, 95),
        "PHEV":   _lerp(style_composite, 30, 85, 42, 88),
        "HEV":    _lerp(style_composite, 30, 85, 45, 80),
        "Petrol": _lerp(style_composite, 30, 85, 85, 42),
        "Diesel": _lerp(style_composite, 30, 85, 72, 42),
    }

    # F5: Short Trip Density
    f5 = {
        "BEV":    _sigmoid_score(short_trip_pct, 35, 0.08, 45, 95),
        "PHEV":   _sigmoid_score(short_trip_pct, 30, 0.06, 52, 85),
        "HEV":    _sigmoid_score(short_trip_pct, 30, 0.06, 52, 88),
        "Petrol": _sigmoid_score(short_trip_pct, 40, -0.06, 35, 78),
        "Diesel": _sigmoid_score(short_trip_pct, 35, -0.08, 22, 82),
    }

    # F6: Fuel Efficiency Pressure (high consumption → push toward electrified)
    f6 = {
        "BEV":    _lerp(avg_fuel, 4, 9, 55, 95),
        "PHEV":   _lerp(avg_fuel, 4, 9, 58, 88),
        "HEV":    _lerp(avg_fuel, 4, 9, 65, 78),
        "Petrol": _lerp(avg_fuel, 4, 9, 72, 38),
        "Diesel": _lerp(avg_fuel, 4, 9, 68, 52),
    }

    # F7: Speed Behavior (disciplined → EV friendly)
    f7 = {
        "BEV":    _lerp(speed_discipline, 30, 85, 35, 92),
        "PHEV":   _lerp(speed_discipline, 30, 85, 48, 88),
        "HEV":    _lerp(speed_discipline, 30, 85, 45, 88),
        "Petrol": _lerp(speed_discipline, 30, 85, 85, 48),
        "Diesel": _lerp(speed_discipline, 30, 85, 80, 48),
    }

    # F8: Driver Archetype (categorical)
    archetype_scores = {
        "Eco Expert":      {"BEV": 95, "PHEV": 85, "HEV": 85, "Petrol": 30, "Diesel": 25},
        "City Navigator":  {"BEV": 90, "PHEV": 80, "HEV": 85, "Petrol": 45, "Diesel": 25},
        "Smooth Cruiser":  {"BEV": 80, "PHEV": 80, "HEV": 85, "Petrol": 60, "Diesel": 50},
        "Highway Warrior": {"BEV": 40, "PHEV": 60, "HEV": 55, "Petrol": 80, "Diesel": 90},
        "Spirited Driver": {"BEV": 35, "PHEV": 50, "HEV": 45, "Petrol": 90, "Diesel": 75},
    }
    f8 = archetype_scores.get(driver_class, {"BEV": 60, "PHEV": 74, "HEV": 70, "Petrol": 70, "Diesel": 62})

    # F9: Seasonal Resilience (winter range loss, fuel spike)
    if seasonal and seasonal.get("ev_ratio") and len(seasonal["ev_ratio"]) == 4:
        winter_ev = seasonal["ev_ratio"][0]
        summer_ev = seasonal["ev_ratio"][2]
        winter_fuel = seasonal["avg_fuel"][0] if seasonal.get("avg_fuel") and len(seasonal["avg_fuel"]) > 2 else 0
        summer_fuel = seasonal["avg_fuel"][2] if seasonal.get("avg_fuel") and len(seasonal["avg_fuel"]) > 2 else 0
        ev_drop = max(0, summer_ev - winter_ev)
        fuel_spike = max(0, winter_fuel - summer_fuel)
        f9 = {
            "BEV":    max(20, 88 - ev_drop * 1.8 - fuel_spike * 4),
            "PHEV":   min(92, 75 + ev_drop * 0.3),
            "HEV":    min(85, 72 + fuel_spike * 2),
            "Petrol": _lerp(fuel_spike, 0, 2, 65, 72),
            "Diesel": _lerp(fuel_spike, 0, 2, 68, 75),
        }
    else:
        f9 = dict(NEUTRAL)

    # F10: Idle Time Impact (EV/HEV waste nothing at idle; diesel worst)
    idle_pct = kpis.get("idle_pct", 0) if kpis else 0
    f10 = {
        "BEV":    _lerp(idle_pct, 2, 20, 60, 98),
        "PHEV":   _lerp(idle_pct, 2, 20, 58, 88),
        "HEV":    _lerp(idle_pct, 2, 20, 55, 82),
        "Petrol": _lerp(idle_pct, 2, 20, 70, 42),
        "Diesel": _lerp(idle_pct, 2, 20, 68, 28),
    }

    # F11: Journey Feasibility (long trip break patterns for charging opportunity)
    if journeys:
        long_journeys = [j for j in journeys if j["distance"] > 250]
        unchargeable = sum(1 for j in long_journeys if not any(b >= 20 for b in j.get("breaks", [])))
        long_frac = len(long_journeys) / max(len(journeys), 1)
        f11 = {
            "BEV":    max(20, 88 - unchargeable * 12 - long_frac * 30),
            "PHEV":   max(40, 85 - unchargeable * 5),
            "HEV":    _lerp(long_frac, 0, 0.5, 75, 60),
            "Petrol": _lerp(long_frac, 0, 0.5, 65, 82),
            "Diesel": _lerp(long_frac, 0, 0.5, 60, 90),
        }
    else:
        f11 = dict(NEUTRAL)

    # F12: Regen Braking Potential (brake score + city driving)
    brake_scores = [tr["score_brake"] for tr in trips if tr.get("score_brake") is not None]
    avg_brake = sum(brake_scores) / len(brake_scores) if brake_scores else 50
    regen_potential = 0.6 * (avg_brake / 100) + 0.4 * (city_pct / 100)
    rp = regen_potential * 100
    f12 = {
        "BEV":    _lerp(rp, 20, 80, 48, 95),
        "PHEV":   _lerp(rp, 20, 80, 48, 88),
        "HEV":    _lerp(rp, 20, 80, 48, 92),
        "Petrol": 62,
        "Diesel": 62,
    }

    # F13: Charging Alignment (predictable commuter schedule → BEV friendly)
    is_commuter = trips_per_day >= 1.5 and (6 <= peak_hour <= 9 or 16 <= peak_hour <= 19)
    regularity = _lerp(trips_per_day, 0.5, 3, 40, 90)
    f13 = {
        "BEV":    max(20, min(95, regularity + (10 if is_commuter else 0))),
        "PHEV":   max(20, min(95, regularity * 0.85 + (5 if is_commuter else 0))),
        "HEV":    65,
        "Petrol": _lerp(trips_per_day, 0.5, 3, 72, 55),
        "Diesel": _lerp(trips_per_day, 0.5, 3, 70, 52),
    }

    # F14: Power Mode Aggressiveness (high power → favors petrol; drains BEV)
    total_power_sec = sum(tr.get("power_time_sec", 0) for tr in trips)
    power_ratio = (total_power_sec / total_dur * 100) if total_dur > 0 else 0
    f14 = {
        "BEV":    _lerp(power_ratio, 2, 25, 80, 25),
        "PHEV":   _lerp(power_ratio, 2, 25, 72, 45),
        "HEV":    _lerp(power_ratio, 2, 25, 72, 52),
        "Petrol": _lerp(power_ratio, 2, 25, 52, 90),
        "Diesel": _lerp(power_ratio, 2, 25, 55, 72),
    }

    # F15: Cost Sensitivity (high cost/km or rising costs → push toward electrified)
    cost_per_km = kpis.get("cost_per_km", 0) if kpis else 0
    cost_rising = False
    if monthly and monthly.get("fuel_cost") and len(monthly["fuel_cost"]) >= 6:
        recent_3 = sum(monthly["fuel_cost"][-3:]) / 3
        prev_3 = sum(monthly["fuel_cost"][-6:-3]) / 3
        if prev_3 > 0 and (recent_3 - prev_3) / prev_3 > 0.05:
            cost_rising = True
    cost_pressure = _lerp(cost_per_km, 0.08, 0.50, 0, 100)
    if cost_rising:
        cost_pressure = min(100, cost_pressure + 15)
    f15 = {
        "BEV":    _lerp(cost_pressure, 0, 100, 52, 92),
        "PHEV":   _lerp(cost_pressure, 0, 100, 50, 85),
        "HEV":    _lerp(cost_pressure, 0, 100, 52, 82),
        "Petrol": _lerp(cost_pressure, 0, 100, 78, 38),
        "Diesel": _lerp(cost_pressure, 0, 100, 72, 48),
    }

    # F16: Usage Pattern (weekday commuter → BEV; weekend leisure → PHEV/ICE)
    f16 = {
        "BEV":    _lerp(weekend_pct, 15, 70, 88, 42),
        "PHEV":   _lerp(weekend_pct, 15, 70, 72, 82),
        "HEV":    _lerp(weekend_pct, 15, 70, 78, 65),
        "Petrol": _lerp(weekend_pct, 15, 70, 55, 78),
        "Diesel": _lerp(weekend_pct, 15, 70, 50, 72),
    }

    factors = [f1, f2, f3, f4, f5, f6, f7, f8, f9, f10, f11, f12, f13, f14, f15, f16]

    # Compute weighted scores + per-factor breakdown
    factor_breakdown = {}
    scores = {}
    for eng in ENGINE_KEYS:
        factor_breakdown[eng] = {}
        total = 0
        for f, w, name in zip(factors, weights, factor_names):
            s = f[eng]
            factor_breakdown[eng][name] = round(s, 1)
            total += s * w
        scores[eng] = round(total, 1)

    sorted_engines = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_eng = sorted_engines[0][0]
    runner_eng = sorted_engines[1][0]

    # Determine current engine type (needed for savings baseline)
    if engine_type in ("BEV", "PHEV", "HEV", "Petrol", "Diesel"):
        current_engine = engine_type
    elif fuel_type == "diesel":
        current_engine = "Diesel"
    elif ev_pct > 0:
        current_engine = "HEV"
    else:
        current_engine = "Petrol"

    # ==================================================================
    # ESTIMATED SAVINGS
    # ==================================================================
    savings = _estimate_savings(kpis, monthly, price_fn, currency_symbol, current_engine)

    # ==================================================================
    # RISK FACTORS
    # ==================================================================
    risks_map: dict[str, list[dict]] = {e: [] for e in ENGINE_KEYS}

    # BEV winter range risk
    if seasonal and seasonal.get("ev_ratio") and len(seasonal["ev_ratio"]) == 4:
        s_ev = seasonal["ev_ratio"][2]  # summer
        w_ev = seasonal["ev_ratio"][0]  # winter
        if s_ev > 0 and (s_ev - w_ev) / max(s_ev, 1) > 0.40:
            risks_map["BEV"].append({
                "label": t.get("risk_bev_winter", "Winter range loss") if t else "Winter range loss",
                "detail": (t.get("risk_bev_winter_detail",
                    "EV ratio drops from {s:.0f}% in summer to {w:.0f}% in winter — expect 20-30% range reduction")
                    if t else "EV ratio drops from {s:.0f}% in summer to {w:.0f}% in winter — expect 20-30% range reduction"
                ).format(s=s_ev, w=w_ev),
                "severity": "high",
            })

    # BEV long trips without charging breaks
    if journeys:
        long_no_break = [j for j in journeys if j["distance"] > 250
                         and not any(b >= 20 for b in j.get("breaks", []))]
        if long_no_break:
            risks_map["BEV"].append({
                "label": t.get("risk_bev_long_trips", "Long trips without charging stops") if t else "Long trips without charging stops",
                "detail": (t.get("risk_bev_long_trips_detail",
                    "{n} of your top journeys exceed 250 km with no 20+ min break for charging")
                    if t else "{n} of your top journeys exceed 250 km with no 20+ min break for charging"
                ).format(n=len(long_no_break)),
                "severity": "high" if len(long_no_break) >= 3 else "medium",
            })

    # BEV cross-border charging
    countries_visited = kpis.get("countries_visited", 0) if kpis else 0
    if countries_visited > 2:
        risks_map["BEV"].append({
            "label": t.get("risk_bev_cross_border", "Cross-border charging") if t else "Cross-border charging",
            "detail": (t.get("risk_bev_cross_border_detail",
                "Driving across {n} countries means varying charging networks and payment systems")
                if t else "Driving across {n} countries means varying charging networks and payment systems"
            ).format(n=countries_visited),
            "severity": "medium",
        })

    # Diesel idle / DPF risk
    if idle_pct > 10:
        idle_hours = kpis.get("idle_hours", 0) if kpis else 0
        risks_map["Diesel"].append({
            "label": t.get("risk_diesel_idle", "High idle time (DPF wear)") if t else "High idle time (DPF wear)",
            "detail": (t.get("risk_diesel_idle_detail",
                "{pct:.0f}% idle time ({hrs:.0f}h total) accelerates DPF degradation and emissions")
                if t else "{pct:.0f}% idle time ({hrs:.0f}h total) accelerates DPF degradation and emissions"
            ).format(pct=idle_pct, hrs=idle_hours),
            "severity": "high",
        })

    # Diesel short trips
    if short_trip_pct > 60:
        risks_map["Diesel"].append({
            "label": t.get("risk_diesel_short_trips", "Too many short trips") if t else "Too many short trips",
            "detail": (t.get("risk_diesel_short_trips_detail",
                "{pct:.0f}% trips under 15 km prevent DPF regeneration and cause excessive engine wear")
                if t else "{pct:.0f}% trips under 15 km prevent DPF regeneration and cause excessive engine wear"
            ).format(pct=short_trip_pct),
            "severity": "high",
        })

    # Petrol rising costs
    if cost_rising:
        risks_map["Petrol"].append({
            "label": t.get("risk_petrol_rising_costs", "Rising fuel costs") if t else "Rising fuel costs",
            "detail": (t.get("risk_petrol_rising_costs_detail",
                "Your monthly fuel costs are trending upward — electrified options absorb price volatility better")
                if t else "Your monthly fuel costs are trending upward — electrified options absorb price volatility better"),
            "severity": "medium",
        })

    # ==================================================================
    # WHY YES REASONS — strength-sorted, top 5
    # ==================================================================
    reasons_map: dict[str, list[tuple[float, str]]] = {e: [] for e in ENGINE_KEYS}

    if short_trip_pct > 30:
        s = short_trip_pct / 100
        reasons_map["BEV"].append((s, (t["reason_bev_short_trips"] if t else "{pct:.0f}% of your trips are under 15 km — perfect for battery range").format(pct=short_trip_pct)))
        reasons_map["PHEV"].append((s * 0.8, (t["reason_phev_short_trips"] if t else "{pct:.0f}% short trips can run on pure electric").format(pct=short_trip_pct)))

    if city_pct > 45:
        s = city_pct / 100
        reasons_map["BEV"].append((s, (t["reason_bev_city"] if t else "{pct:.0f}% city driving maximizes regenerative braking").format(pct=city_pct)))
        reasons_map["HEV"].append((s * 0.9, (t["reason_hev_city"] if t else "{pct:.0f}% city driving is where hybrids shine most").format(pct=city_pct)))
        reasons_map["PHEV"].append((s * 0.85, (t["reason_phev_city"] if t else "{pct:.0f}% city driving enables frequent EV mode").format(pct=city_pct)))

    if ev_pct > 10:
        s = min(1.0, ev_pct / 100 * 1.5)
        reasons_map["BEV"].append((s, (t["reason_bev_ev_ready"] if t else "Already {pct:.0f}% EV driving shows readiness for full electric").format(pct=ev_pct)))
        reasons_map["PHEV"].append((s * 0.9, (t["reason_phev_ev_usage"] if t else "Your {pct:.0f}% EV usage would increase with a larger battery").format(pct=ev_pct)))

    if eco_score >= 55:
        s = eco_score / 100
        reasons_map["BEV"].append((s, t["reason_bev_eco_style"] if t else "Your eco-conscious style maximizes EV efficiency"))
        reasons_map["HEV"].append((s * 0.9, t["reason_hev_eco_style"] if t else "Your eco-conscious driving optimizes hybrid regeneration"))

    if avg_trip_km > 50:
        s = min(1.0, avg_trip_km / 150)
        reasons_map["Diesel"].append((s, (t["reason_diesel_long_trip"] if t else "Average trip of {km:.0f} km favors diesel efficiency at cruise").format(km=avg_trip_km)))
        reasons_map["Petrol"].append((s * 0.8, (t["reason_petrol_long_trip"] if t else "Your {km:.0f} km average trip suits petrol's highway comfort").format(km=avg_trip_km)))

    if highway_pct > 40:
        s = highway_pct / 100
        reasons_map["Diesel"].append((s, (t["reason_diesel_highway"] if t else "{pct:.0f}% highway driving is diesel's sweet spot").format(pct=highway_pct)))
        reasons_map["Petrol"].append((s * 0.8, (t["reason_petrol_highway"] if t else "{pct:.0f}% highway driving suits petrol turbo engines").format(pct=highway_pct)))

    if calmness < 40:
        reasons_map["Petrol"].append((0.7, t["reason_petrol_spirited"] if t else "Your spirited driving style pairs well with responsive petrol engines"))

    if avg_fuel > 5.5:
        s = min(1.0, avg_fuel / 10)
        reasons_map["HEV"].append((s, (t["reason_hev_fuel_savings"] if t else "At {fuel:.1f} L/100km, a hybrid could cut consumption by 20-30%").format(fuel=avg_fuel)))
        reasons_map["PHEV"].append((s * 0.9, (t["reason_phev_fuel_savings"] if t else "At {fuel:.1f} L/100km, a PHEV could slash your fuel costs").format(fuel=avg_fuel)))

    if trips_per_day >= 1.5:
        reasons_map["BEV"].append((0.6, (t["reason_bev_daily_trips"] if t else "Your {n:.1f} daily trips are ideal for overnight home charging").format(n=trips_per_day)))

    if weekend_pct > 55:
        reasons_map["PHEV"].append((0.5, t["reason_phev_weekend"] if t else "Weekend-heavy use benefits from petrol backup on longer leisure trips"))

    if speed_discipline < 45:
        reasons_map["Petrol"].append((0.5, t["reason_petrol_speed"] if t else "Your frequent high-speed driving suits petrol's broad RPM range"))

    if consistency >= 65:
        reasons_map["BEV"].append((0.55, t["reason_bev_consistency"] if t else "Your consistent driving routine makes home charging planning easy"))

    # New: Idle savings
    if idle_pct > 8:
        idle_hours_val = kpis.get("idle_hours", 0) if kpis else 0
        waste_l = idle_hours_val * 0.8
        reasons_map["BEV"].append((min(1.0, idle_pct / 100 * 1.5), (t.get("reason_bev_idle_savings",
            "Your {pct:.0f}% idle time wastes ~{liters:.0f}L of fuel — zero waste with BEV")
            if t else "Your {pct:.0f}% idle time wastes ~{liters:.0f}L of fuel — zero waste with BEV"
        ).format(pct=idle_pct, liters=waste_l)))
        reasons_map["HEV"].append((idle_pct / 100, (t.get("reason_hev_idle_savings",
            "Hybrid auto-stops during your {pct:.0f}% idle time, saving fuel")
            if t else "Hybrid auto-stops during your {pct:.0f}% idle time, saving fuel"
        ).format(pct=idle_pct)))

    # New: Regen braking
    if avg_brake >= 60 and city_pct > 50:
        reasons_map["BEV"].append((0.65, (t.get("reason_bev_regen",
            "Your braking score of {score:.0f} maximizes regenerative energy recovery")
            if t else "Your braking score of {score:.0f} maximizes regenerative energy recovery"
        ).format(score=avg_brake)))
        reasons_map["HEV"].append((0.55, (t.get("reason_hev_regen",
            "Your braking discipline (score {score:.0f}) enhances hybrid regeneration")
            if t else "Your braking discipline (score {score:.0f}) enhances hybrid regeneration"
        ).format(score=avg_brake)))

    # New: Seasonal PHEV flexibility
    if seasonal and seasonal.get("ev_ratio") and len(seasonal["ev_ratio"]) == 4:
        s_ev_r = seasonal["ev_ratio"][2]
        w_ev_r = seasonal["ev_ratio"][0]
        if s_ev_r > 15 and (s_ev_r - w_ev_r) > 10:
            reasons_map["PHEV"].append((0.6, (t.get("reason_phev_seasonal",
                "PHEV adapts to seasons: EV in summer ({s:.0f}%), petrol backup in winter")
                if t else "PHEV adapts to seasons: EV in summer ({s:.0f}%), petrol backup in winter"
            ).format(s=s_ev_r)))

    # New: Cost savings (only for engines that are cheaper than current)
    if savings and savings.get("by_engine"):
        for eng_key in ["BEV", "PHEV", "HEV"]:
            eng_sav = savings["by_engine"].get(eng_key, {})
            pct_sav = eng_sav.get("pct_saving", 0)
            monthly_sav = eng_sav.get("monthly_saving", 0)
            if pct_sav > 10 and not eng_sav.get("is_current"):
                sym = savings.get("currency_symbol", "zl")
                reasons_map[eng_key].append((pct_sav / 100, (t.get("reason_cost_savings",
                    "Estimated {pct:.0f}% fuel cost reduction — saving ~{amount:.0f} {sym}/month")
                    if t else "Estimated {pct:.0f}% fuel cost reduction — saving ~{amount:.0f} {sym}/month"
                ).format(pct=pct_sav, amount=monthly_sav, sym=sym)))

    # New: Diesel consistency
    if consistency >= 70 and highway_pct > 40:
        reasons_map["Diesel"].append((0.55, (t.get("reason_diesel_consistency",
            "Your consistency score of {score:.0f} matches diesel's constant-speed efficiency")
            if t else "Your consistency score of {score:.0f} matches diesel's constant-speed efficiency"
        ).format(score=consistency)))

    # BEV long trip note
    if max_trip_km > 300:
        reasons_map["BEV"].append((0.3, t["reason_bev_long_trip_note"] if t else "Note: your longest recorded trip exceeds 300 km — BEV charging stops required"))

    # Sort by strength, keep top 5 text only
    for eng in ENGINE_KEYS:
        reasons_map[eng].sort(key=lambda x: x[0], reverse=True)
        reasons_map[eng] = [text for _, text in reasons_map[eng][:5]]

    # Fallback reasons
    fallbacks = {
        "BEV": [t["reason_bev_fallback_1"] if t else "Zero emissions and lowest running costs",
                t["reason_bev_fallback_2"] if t else "Best for daily commutes and urban driving"],
        "PHEV": [t["reason_phev_fallback_1"] if t else "Flexibility of electric for short trips with petrol backup",
                 t["reason_phev_fallback_2"] if t else "Good balance of efficiency and range"],
        "HEV": [t["reason_hev_fallback_1"] if t else "No charging needed with self-charging hybrid system",
                t["reason_hev_fallback_2"] if t else "Great fuel efficiency in mixed driving"],
        "Petrol": [t["reason_petrol_fallback_1"] if t else "Wide availability and lower purchase price",
                   t["reason_petrol_fallback_2"] if t else "Good for varied driving conditions"],
        "Diesel": [t["reason_diesel_fallback_1"] if t else "Best highway fuel economy for long distances",
                   t["reason_diesel_fallback_2"] if t else "High torque for heavy loads"],
    }
    for eng in ENGINE_KEYS:
        if not reasons_map[eng]:
            reasons_map[eng] = fallbacks[eng]

    # ==================================================================
    # WHY NOT REASONS — strength-sorted, up to 4
    # ==================================================================
    why_not_map: dict[str, list[tuple[float, str]]] = {e: [] for e in ENGINE_KEYS}

    tradeoffs_map = {
        "BEV": t["tradeoff_bev"] if t else "Requires charging infrastructure; range limited on long highway trips",
        "PHEV": t["tradeoff_phev"] if t else "Higher purchase price; needs regular charging to maximize savings",
        "HEV": t["tradeoff_hev"] if t else "Less electric range than PHEV/BEV; still burns fuel for all trips",
        "Petrol": t["tradeoff_petrol"] if t else "Higher fuel costs; more CO2 emissions than electrified options",
        "Diesel": t["tradeoff_diesel"] if t else "Higher emissions in city; declining resale value in some markets",
    }

    # BEV
    if max_trip_km > 200:
        why_not_map["BEV"].append((0.9, (t["why_not_bev_long_trip"] if t else "Your longest trip ({km:.0f} km) would require a mid-journey charging stop").format(km=max_trip_km)))
    if highway_pct > 50:
        why_not_map["BEV"].append((0.7, (t["why_not_bev_highway"] if t else "{pct:.0f}% highway driving reduces BEV range and regeneration efficiency").format(pct=highway_pct)))
    if speed_discipline < 45:
        why_not_map["BEV"].append((0.6, t["why_not_bev_speed"] if t else "Frequent high-speed driving significantly drains the battery"))
    if seasonal and seasonal.get("ev_ratio") and len(seasonal["ev_ratio"]) == 4:
        s_ev_wn = seasonal["ev_ratio"][2]
        w_ev_wn = seasonal["ev_ratio"][0]
        if s_ev_wn > 10 and (s_ev_wn - w_ev_wn) / max(s_ev_wn, 1) > 0.30:
            why_not_map["BEV"].append((0.65, (t.get("why_not_bev_winter",
                "Winter EV ratio drops to {w:.0f}% from {s:.0f}% — expect 20-30% range loss")
                if t else "Winter EV ratio drops to {w:.0f}% from {s:.0f}% — expect 20-30% range loss"
            ).format(w=w_ev_wn, s=s_ev_wn)))
    if journeys:
        long_j = [j for j in journeys if j["distance"] > 300]
        if long_j:
            avg_long_km = sum(j["distance"] for j in long_j) / len(long_j)
            why_not_map["BEV"].append((0.75, (t.get("why_not_bev_journeys",
                "{n} of your top journeys exceed 300 km (avg {avg:.0f} km) — 1-2 charging stops each")
                if t else "{n} of your top journeys exceed 300 km (avg {avg:.0f} km) — 1-2 charging stops each"
            ).format(n=len(long_j), avg=avg_long_km)))

    # PHEV
    if ev_pct < 5 and total_dist > 5000:
        why_not_map["PHEV"].append((0.8, t["why_not_phev_no_charging"] if t else "Without regular plugging-in, a PHEV effectively becomes a heavy petrol car"))
    if avg_trip_km > 70:
        why_not_map["PHEV"].append((0.6, (t["why_not_phev_long_avg"] if t else "Long trips of {km:.0f} km average will mostly run on petrol, limiting EV benefit").format(km=avg_trip_km)))

    # HEV
    if ev_pct > 20:
        why_not_map["HEV"].append((0.7, (t.get("why_not_hev_ev_appetite_v2",
            "Your {pct:.0f}% EV appetite exceeds standard hybrid capacity — consider PHEV")
            if t else "Your {pct:.0f}% EV appetite exceeds standard hybrid capacity — consider PHEV"
        ).format(pct=ev_pct)))
    elif ev_pct > 15:
        why_not_map["HEV"].append((0.5, t["why_not_hev_ev_appetite"] if t else "Your EV appetite would be better served by a PHEV's larger battery"))
    if highway_pct > 55:
        why_not_map["HEV"].append((0.5, t["why_not_hev_highway"] if t else "Highway-heavy driving reduces hybrid regeneration benefit"))

    # Petrol
    if short_trip_pct > 40:
        why_not_map["Petrol"].append((0.7, (t["why_not_petrol_short_trips"] if t else "{pct:.0f}% short trips cause cold-start engine wear and poor efficiency").format(pct=short_trip_pct)))
    if city_pct > 60:
        why_not_map["Petrol"].append((0.65, (t["why_not_petrol_city"] if t else "{pct:.0f}% city driving will hurt petrol fuel economy vs a hybrid").format(pct=city_pct)))
    if smoothness >= 70 and eco_score >= 55:
        why_not_map["Petrol"].append((0.5, t["why_not_petrol_eco"] if t else "Your eco-conscious style is better rewarded in an electrified powertrain"))
    if cost_rising:
        why_not_map["Petrol"].append((0.55, t.get("why_not_petrol_rising_costs",
            "Your fuel costs trending up — electrified options absorb price volatility better")
            if t else "Your fuel costs trending up — electrified options absorb price volatility better"))

    # Diesel
    if city_pct > 60:
        why_not_map["Diesel"].append((0.8, (t["why_not_diesel_city"] if t else "{pct:.0f}% city driving risks DPF clogging and high urban NOx emissions").format(pct=city_pct)))
    if short_trip_pct > 40:
        why_not_map["Diesel"].append((0.7, t["why_not_diesel_short"] if t else "Short trips damage diesel DPF filters and increase cold-start wear"))
    if avg_trip_km < 30:
        why_not_map["Diesel"].append((0.6, (t["why_not_diesel_avg_short"] if t else "Diesel efficiency gains only appear on sustained runs — your {km:.0f} km average is too short").format(km=avg_trip_km)))
    if idle_pct > 10:
        idle_hrs_wn = kpis.get("idle_hours", 0) if kpis else 0
        why_not_map["Diesel"].append((0.65, (t.get("why_not_diesel_idle",
            "Your {pct:.0f}% idle time ({hrs:.0f}h total) produces emissions and DPF wear")
            if t else "Your {pct:.0f}% idle time ({hrs:.0f}h total) produces emissions and DPF wear"
        ).format(pct=idle_pct, hrs=idle_hrs_wn)))

    # Cost increase why-not for engines costlier than current
    if savings and savings.get("by_engine"):
        for eng_key in ENGINE_KEYS:
            eng_sav = savings["by_engine"].get(eng_key, {})
            pct_sav = eng_sav.get("pct_saving", 0)
            monthly_extra = -eng_sav.get("monthly_saving", 0)
            if pct_sav < -5 and not eng_sav.get("is_current"):
                sym = savings.get("currency_symbol", "zl")
                why_not_map[eng_key].append((0.6, (t.get("why_not_cost_increase",
                    "Would cost ~{amount:.0f} {sym}/month more than your current {current} ({pct:.0f}% increase)")
                    if t else "Would cost ~{amount:.0f} {sym}/month more than your current {current} ({pct:.0f}% increase)"
                ).format(amount=monthly_extra, sym=sym, current=current_engine, pct=abs(pct_sav))))

    # Sort by strength, take top 4; fallback to tradeoff
    for eng in ENGINE_KEYS:
        why_not_map[eng].sort(key=lambda x: x[0], reverse=True)
        why_not_map[eng] = [text for _, text in why_not_map[eng][:4]]
        if not why_not_map[eng]:
            why_not_map[eng] = [tradeoffs_map[eng]]

    # ==================================================================
    # TOP FACTORS PER ENGINE
    # ==================================================================
    top_factors_map = {}
    for eng in ENGINE_KEYS:
        fb = factor_breakdown[eng]
        sorted_fb = sorted(fb.items(), key=lambda x: x[1], reverse=True)
        top_factors_map[eng] = [name for name, _ in sorted_fb[:3]]

    # ==================================================================
    # BUILD RESULT
    # ==================================================================
    result_scores = []
    for eng, score in sorted_engines:
        eng_savings = savings["by_engine"][eng] if savings and savings.get("by_engine") else None
        result_scores.append({
            "type": eng,
            "label": engines[eng]["label"],
            "score": score,
            "reasons": reasons_map[eng],
            "why_not": why_not_map[eng],
            "risks": risks_map[eng],
            "savings": eng_savings,
            "top_factors": top_factors_map[eng],
        })

    return {
        "scores": result_scores,
        "current_engine": current_engine,
        "recommendation": {
            "type": top_eng,
            "label": engines[top_eng]["label"],
            "score": scores[top_eng],
            "reasons": reasons_map[top_eng][:5],
            "tradeoffs": tradeoffs_map[top_eng],
        },
        "runner_up": {
            "type": runner_eng,
            "label": engines[runner_eng]["label"],
            "score": scores[runner_eng],
            "reasons": reasons_map[runner_eng][:3],
            "tradeoffs": tradeoffs_map[runner_eng],
        },
        "factor_breakdown": factor_breakdown,
        "factor_names": factor_names,
        "factor_labels": factor_labels,
        "savings": savings,
    }


def build_html(kpis, monthly, weekday_hour, score_dist, heatmap_layers, longest_trips,
               trip_cats, seasonal, trips, driving_modes, speed_analytics,
               highway_city, night_driving, idle_trend, service_history, odometer_data,
               driving_profile=None, engine_recommendation=None,
               vehicle=None, currency_code="PLN", currency_symbol="zl",
               country_code="PL", fuel_type="gasoline", t=None, lang="en"):
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

    # Build JS translation subset (js_* keys + a few extras needed in chart labels)
    js_t = {k[3:]: v for k, v in (t or {}).items() if k.startswith("js_")}
    for extra_key in ("night", "day", "highway", "city_other", "best_match", "runner_up",
                       "tradeoffs_label", "why_yes_label", "why_not_label", "your_car",
                       "factor_breakdown_label", "risk_factors_label",
                       "estimated_savings_label", "estimated_cost_change_label",
                       "your_current_cost_label", "monthly_cost_label", "annual_cost_label",
                       "eng_tab_why", "eng_tab_analysis", "eng_tab_cost"):
        if t and extra_key in t:
            js_t[extra_key] = t[extra_key]

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
        "currency": {"code": currency_code, "symbol": currency_symbol},
        "drivingProfile": driving_profile or {},
        "engineRecommendation": engine_recommendation or {},
    }

    html = f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{vehicle_name} — {t["dashboard_subtitle"]}</title>
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
      <p class="text-muted mt-1">{t["dashboard_subtitle"]} &middot; {t["hybrid"]}</p>
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
        <div>{kpis['total_trips']} {t["trips_recorded"]}</div>
      </div>
    </div>
  </div>

  <!-- KPI Cards Row 1 -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
    <div class="card">
      <div class="kpi-value">{kpis['total_trips']}</div>
      <div class="kpi-label">{t["kpi_total_trips"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_distance_km']:,.0f}<span class="text-lg text-muted"> km</span></div>
      <div class="kpi-label">{t["kpi_total_distance"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['avg_fuel_l100km']}<span class="text-lg text-muted"> L/100</span></div>
      <div class="kpi-label">{t["kpi_avg_fuel"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['ev_ratio_pct']}<span class="text-lg text-muted">%</span></div>
      <div class="kpi-label">{t["kpi_electric_driving"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['avg_score']}</div>
      <div class="kpi-label">{t["kpi_avg_score"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_hours']:,.0f}<span class="text-lg text-muted"> h</span></div>
      <div class="kpi-label">{t["kpi_time_driving"]}</div>
    </div>
  </div>

  <!-- KPI Cards Row 2 — Cost & Environment -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
    <div class="card">
      <div class="kpi-value">{kpis['total_cost']:,.0f}<span class="text-lg text-muted"> {currency_code}</span></div>
      <div class="kpi-label">{t["kpi_total_fuel_cost"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['cost_per_km']}<span class="text-lg text-muted"> {currency_code}/km</span></div>
      <div class="kpi-label">{t["kpi_cost_per_km"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_fuel_l']:,.0f}<span class="text-lg text-muted"> L</span></div>
      <div class="kpi-label">{t["kpi_total_fuel_used"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['total_ev_km']:,.0f}<span class="text-lg text-muted"> km</span></div>
      <div class="kpi-label">{t["kpi_ev_distance"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['co2_emitted_kg']:,.0f}<span class="text-lg text-muted"> kg</span></div>
      <div class="kpi-label">{t["kpi_co2_emitted"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value" style="color:#22c55e">{kpis['co2_saved_kg']:,.0f}<span class="text-lg text-muted"> kg</span></div>
      <div class="kpi-label">{t["kpi_co2_saved"]}</div>
    </div>
  </div>

  <!-- KPI Cards Row 3 — Speed, Highway, Night -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
    <div class="card">
      <div class="kpi-value">{kpis['avg_speed_kmh']}<span class="text-lg text-muted"> km/h</span></div>
      <div class="kpi-label">{t["kpi_avg_speed"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['max_speed_ever']}<span class="text-lg text-muted"> km/h</span></div>
      <div class="kpi-label">{t["kpi_max_speed"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['highway_pct']}<span class="text-lg text-muted">%</span></div>
      <div class="kpi-label">{t["kpi_highway_distance"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['idle_pct']}<span class="text-lg text-muted">%</span></div>
      <div class="kpi-label">{t["kpi_idle_time"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['night_trip_count']}</div>
      <div class="kpi-label">{t["kpi_night_trips"]}</div>
    </div>
    <div class="card">
      <div class="kpi-value">{kpis['countries_visited']}</div>
      <div class="kpi-label">{t["kpi_countries"]} <span class="text-xs text-faint">({countries_str})</span></div>
    </div>
  </div>

  <!-- Tab Navigation -->
  <div class="flex gap-2 mb-8 flex-wrap" id="tabBar">
    <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium bg-lexus-600 text-white transition-colors" data-tab="overview" onclick="switchTab('overview')">{t["tab_overview"]}</button>
    <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium heat-btn-inactive transition-colors" data-tab="fuel-ev" onclick="switchTab('fuel-ev')">{t["tab_fuel_ev"]}</button>
    <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium heat-btn-inactive transition-colors" data-tab="driving" onclick="switchTab('driving')">{t["tab_driving"]}</button>
    <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium heat-btn-inactive transition-colors" data-tab="trips" onclick="switchTab('trips')">{t["tab_trips"]}</button>
    <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium heat-btn-inactive transition-colors" data-tab="profile" onclick="switchTab('profile')">{t["tab_profile"]}</button>
  </div>

  <div id="tab-overview" data-tabpanel>
  <!-- Heatmap with Layer Toggle -->
  <div class="card mb-8 !p-0 overflow-hidden">
    <div class="p-6 pb-2 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
      <div>
        <h2 class="text-xl font-semibold text-heading">{t["heatmap_title"]}</h2>
        <p class="text-sm text-muted">{kpis['total_trips']} {t["heatmap_desc_prefix"]} &middot; {t["heatmap_desc_suffix"]} &middot; <span id="heatmapPts"></span> {t["heatmap_waypoints"]}</p>
      </div>
      <div class="flex gap-2 flex-wrap">
        <button class="heat-btn px-3 py-1 rounded-full text-sm text-white bg-lexus-600 transition-colors" data-layer="all" onclick="switchHeatLayer('all')">{t["heatmap_all"]}</button>
        <button class="heat-btn px-3 py-1 rounded-full text-sm heat-btn-inactive transition-colors" data-layer="ev" onclick="switchHeatLayer('ev')">{t["heatmap_ev"]}</button>
        <button class="heat-btn px-3 py-1 rounded-full text-sm heat-btn-inactive transition-colors" data-layer="highway" onclick="switchHeatLayer('highway')">{t["heatmap_highway"]}</button>
        <button class="heat-btn px-3 py-1 rounded-full text-sm heat-btn-inactive transition-colors" data-layer="overspeed" onclick="switchHeatLayer('overspeed')">{t["heatmap_over_limit"]}</button>
      </div>
    </div>
    <div id="heatmap"></div>
  </div>

  <!-- Monthly Charts Row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["monthly_distance"]}</h3>
      <canvas id="monthlyDistance"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["monthly_fuel_cost"]} ({currency_code})</h3>
      <canvas id="monthlyCost"></canvas>
    </div>
  </div>

  <!-- Fuel Efficiency Trend -->
  <div class="card mb-8">
    <h3 class="text-lg font-semibold text-heading mb-4">{t["fuel_efficiency_trend"]}</h3>
    <div style="height:200px"><canvas id="fuelTrend"></canvas></div>
  </div>
  </div><!-- /tab-overview -->

  <div id="tab-fuel-ev" data-tabpanel style="display:none">
  <!-- Fuel + EV Row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["monthly_fuel_consumption"]}</h3>
      <canvas id="monthlyFuel"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["ev_vs_fuel_distance"]}</h3>
      <canvas id="evIce"></canvas>
    </div>
  </div>

  <!-- Doughnut Charts Row -->
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">{t["drive_mode_time"]}</h3>
      <canvas id="modeTime"></canvas>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">{t["drive_mode_distance"]}</h3>
      <canvas id="modeDist"></canvas>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">{t["night_vs_day"]}</h3>
      <canvas id="nightDay"></canvas>
      <div class="grid grid-cols-2 gap-2 mt-3 text-xs">
        <div class="text-center">
          <div class="text-muted">{t["night"]}</div>
          <div class="text-heading font-semibold" id="nightFuel"></div>
        </div>
        <div class="text-center">
          <div class="text-muted">{t["day"]}</div>
          <div class="text-heading font-semibold" id="dayFuel"></div>
        </div>
      </div>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">{t["trip_categories_title"]}</h3>
      <canvas id="tripCats"></canvas>
    </div>
  </div>

  <!-- Seasonal Charts -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">{t["ev_ratio_by_season"]}</h3>
      <canvas id="seasonalEv"></canvas>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">{t["fuel_by_season"]}</h3>
      <canvas id="seasonalFuel"></canvas>
    </div>
  </div>
  </div><!-- /tab-fuel-ev -->

  <div id="tab-driving" data-tabpanel style="display:none">
  <!-- Speed Analytics -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["max_speed_distribution"]}</h3>
      <canvas id="speedHist"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["monthly_speed_trends"]}</h3>
      <canvas id="speedTrend"></canvas>
    </div>
  </div>

  <!-- Highway vs City -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["highway_vs_city"]}</h3>
      <canvas id="highwayCity"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["idle_time_trend"]}</h3>
      <canvas id="idleTrend"></canvas>
    </div>
  </div>

  <!-- Score Charts -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">{t["monthly_driving_score"]}</h3>
      <canvas id="monthlyScore"></canvas>
    </div>
    <div class="card">
      <h3 class="text-sm font-semibold text-heading mb-3">{t["driving_score_distribution"]}</h3>
      <canvas id="scoreDist"></canvas>
    </div>
  </div>

  <!-- Time Patterns Row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["trips_by_weekday"]}</h3>
      <canvas id="weekday"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["trips_by_hour"]}</h3>
      <canvas id="hourly"></canvas>
    </div>
  </div>

  </div><!-- /tab-driving -->

  <div id="tab-trips" data-tabpanel style="display:none">
  <!-- Top Journeys Table -->
  <div class="card mb-8 overflow-x-auto">
    <div class="flex items-baseline gap-3 mb-4">
      <h3 class="text-lg font-semibold text-heading">{t["longest_journeys"]}</h3>
      <span class="text-xs text-muted">{t["longest_journeys_desc"]}</span>
    </div>
    <table class="w-full text-sm">
      <thead>
        <tr class="text-muted border-b border-themed">
          <th class="text-left py-2 px-3">{t["th_date"]}</th>
          <th class="text-right py-2 px-3">{t["th_distance_km"]}</th>
          <th class="text-right py-2 px-3">{t["th_drive_time"]}</th>
          <th class="text-right py-2 px-3">{t["th_total_time"]}</th>
          <th class="text-right py-2 px-3">{t["th_stops"]}</th>
          <th class="text-right py-2 px-3">{t["th_fuel_l"]}</th>
          <th class="text-right py-2 px-3">{t["th_avg_l100km"]}</th>
          <th class="text-right py-2 px-3">{t["th_cost"]} ({currency_code})</th>
          <th class="text-right py-2 px-3">{t["th_max_kmh"]}</th>
        </tr>
      </thead>
      <tbody id="topTripsBody"></tbody>
    </table>
  </div>

  <!-- Service History -->
  <div class="card mb-8 overflow-x-auto" id="serviceSection" style="display:none">
    <h3 class="text-lg font-semibold text-heading mb-4">{t["service_history"]}</h3>
    <table class="w-full text-sm">
      <thead>
        <tr class="text-muted border-b border-themed">
          <th class="text-left py-2 px-3">{t["th_date"]}</th>
          <th class="text-left py-2 px-3">{t["th_category"]}</th>
          <th class="text-left py-2 px-3">{t["th_provider"]}</th>
          <th class="text-right py-2 px-3">{t["th_odometer_km"]}</th>
          <th class="text-left py-2 px-3">{t["th_notes"]}</th>
        </tr>
      </thead>
      <tbody id="serviceBody"></tbody>
    </table>
  </div>

  <!-- Odometer Tracking -->
  <div class="card mb-8" id="odometerSection" style="display:none">
    <h3 class="text-lg font-semibold text-heading mb-4">{t["odometer_tracking"]}</h3>
    <canvas id="odometerChart"></canvas>
  </div>
  </div><!-- /tab-trips -->

  <div id="tab-profile" data-tabpanel style="display:none">
  <!-- Driving Style Card + Radar -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card flex flex-col justify-center">
      <div class="mb-4">
        <span class="inline-block px-3 py-1 rounded-full text-sm font-semibold bg-lexus-600 text-white" id="profileLabel"></span>
      </div>
      <p class="text-muted text-sm leading-relaxed" id="profileDesc"></p>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["driving_style_radar"]}</h3>
      <div style="max-width:360px;margin:0 auto"><canvas id="radarChart"></canvas></div>
    </div>
  </div>

  <!-- Trip Distance Distribution + Habits -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["trip_distance_distribution"]}</h3>
      <canvas id="tripDistChart"></canvas>
    </div>
    <div class="card">
      <h3 class="text-lg font-semibold text-heading mb-4">{t["driving_habits"]}</h3>
      <div class="grid grid-cols-2 gap-4 mt-2">
        <div class="rounded-xl p-4" style="background:var(--bg-body)">
          <div class="text-2xl font-bold text-heading" id="habitNight"></div>
          <div class="text-sm text-muted">{t["habit_night_driving"]}</div>
        </div>
        <div class="rounded-xl p-4" style="background:var(--bg-body)">
          <div class="text-2xl font-bold text-heading" id="habitWeekend"></div>
          <div class="text-sm text-muted">{t["habit_weekend_trips"]}</div>
        </div>
        <div class="rounded-xl p-4" style="background:var(--bg-body)">
          <div class="text-2xl font-bold text-heading" id="habitTripsDay"></div>
          <div class="text-sm text-muted">{t["habit_trips_per_day"]}</div>
        </div>
        <div class="rounded-xl p-4" style="background:var(--bg-body)">
          <div class="text-2xl font-bold text-heading" id="habitPeakHour"></div>
          <div class="text-sm text-muted">{t["habit_peak_hour"]}</div>
        </div>
        <div class="rounded-xl p-4" style="background:var(--bg-body)">
          <div class="text-2xl font-bold text-heading" id="highwayPctVal"></div>
          <div class="text-sm text-muted">{t["highway"]}</div>
        </div>
        <div class="rounded-xl p-4" style="background:var(--bg-body)">
          <div class="text-2xl font-bold text-heading" id="cityPctVal"></div>
          <div class="text-sm text-muted">{t["city_other"]}</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Engine Recommendation -->
  <div class="card mb-8">
    <h3 class="text-lg font-semibold text-heading mb-6">{t["engine_type_recommendation"]}</h3>
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-8" id="engineScores"></div>
    <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6" id="engineCards"></div>
  </div>
  </div><!-- /tab-profile -->

  <footer class="text-center text-xs text-footer py-8">
    {t["footer_generated"]} {datetime.now().strftime("%Y-%m-%d %H:%M")} &middot; {vehicle_name} — {t["dashboard_subtitle"]}
    &middot; {t["footer_fuel_prices"]}: {fuel_type} ({country_code}) in {currency_code}
  </footer>
</div>

<script>
const D = {json.dumps(data, separators=(',', ':'))};
const T = {json.dumps(js_t, separators=(',', ':'))};

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
      label: T.distance_km,
      data: D.monthly.distance,
      backgroundColor: 'rgba(14,165,233,0.7)',
      borderRadius: 6,
    }}, {{
      label: T.trips,
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
      y: {{ title: {{ display: true, text: T.km }}, grid: {{ color: gridColor }} }},
      y1: {{ position: 'right', title: {{ display: true, text: T.trips }}, grid: {{ display: false }} }},
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
      label: T.fuel_cost + ' (' + D.currency.code + ')',
      data: D.monthly.fuel_cost,
      backgroundColor: 'rgba(234,179,8,0.7)',
      borderRadius: 6,
    }}, {{
      label: T.fuel_price + ' (' + D.currency.code + T.per_l + ')',
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
      y: {{ title: {{ display: true, text: D.currency.code }}, grid: {{ color: gridColor }} }},
      y1: {{ position: 'right', title: {{ display: true, text: D.currency.code + T.per_l }}, grid: {{ display: false }} }},
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
      label: T.l100km,
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
      y: {{ title: {{ display: true, text: T.l100km }}, grid: {{ color: gridColor }} }},
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
      label: T.electric_km,
      data: D.monthly.ev_distance,
      backgroundColor: 'rgba(34,197,94,0.7)',
      borderRadius: 6,
    }}, {{
      label: T.fuel_km,
      data: D.monthly.distance.map((d,i) => Math.max(0, +(d - D.monthly.ev_distance[i]).toFixed(1))),
      backgroundColor: 'rgba(239,68,68,0.5)',
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, title: {{ display: true, text: T.km }}, grid: {{ color: gridColor }} }}
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
      label: T.trips,
      data: D.speedAnalytics.hist_counts,
      backgroundColor: 'rgba(14,165,233,0.7)',
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ title: {{ display: true, text: T.trips }}, grid: {{ color: gridColor }} }},
      x: {{ title: {{ display: true, text: T.max_speed_kmh }}, grid: {{ display: false }} }}
    }}
  }}
}});

// --- Speed Trends ---
createChart('speedTrend', {{
  type: 'line',
  data: {{
    labels: D.speedAnalytics.monthly_labels,
    datasets: [{{
      label: T.avg_speed_kmh,
      data: D.speedAnalytics.monthly_avg,
      borderColor: '#0ea5e9',
      backgroundColor: 'rgba(14,165,233,0.1)',
      fill: true,
      tension: 0.3,
      pointRadius: 4,
    }}, {{
      label: T.max_speed_kmh,
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
      y: {{ title: {{ display: true, text: T.kmh }}, grid: {{ color: gridColor }} }},
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
      label: T.highway_km,
      data: D.highwayCity.highway,
      backgroundColor: 'rgba(59,130,246,0.7)',
      borderRadius: 6,
    }}, {{
      label: T.city_km,
      data: D.highwayCity.city,
      backgroundColor: 'rgba(234,179,8,0.5)',
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, title: {{ display: true, text: T.km }}, grid: {{ color: gridColor }} }}
    }}
  }}
}});

// --- Idle Trend ---
createChart('idleTrend', {{
  type: 'line',
  data: {{
    labels: D.idleTrend.labels,
    datasets: [{{
      label: T.idle_pct,
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
      y: {{ title: {{ display: true, text: T.pct }}, grid: {{ color: gridColor }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// --- Night vs Day (Pie) ---
createChart('nightDay', {{
  type: 'doughnut',
  data: {{
    labels: [T.night, T.day],
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
      label: T.electric_ratio_pct,
      data: D.seasonal.ev_ratio,
      backgroundColor: ['#60a5fa','#34d399','#fbbf24','#f97316'],
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ title: {{ display: true, text: T.pct }}, grid: {{ color: gridColor }}, max: 100 }},
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
      label: T.l100km,
      data: D.seasonal.avg_fuel,
      backgroundColor: ['#60a5fa','#34d399','#fbbf24','#f97316'],
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ title: {{ display: true, text: T.l100km }}, grid: {{ color: gridColor }} }},
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
      label: T.avg_score,
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
      label: T.trips,
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
      y: {{ title: {{ display: true, text: T.trips }}, grid: {{ color: gridColor }} }},
      x: {{ title: {{ display: true, text: T.score_range }}, grid: {{ display: false }} }}
    }}
  }}
}});

// --- Weekday ---
createChart('weekday', {{
  type: 'bar',
  data: {{
    labels: D.weekdayHour.weekday_labels,
    datasets: [{{
      label: T.trips,
      data: D.weekdayHour.weekday_trips,
      backgroundColor: 'rgba(14,165,233,0.7)',
      borderRadius: 6,
    }}, {{
      label: T.distance_km,
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
      y: {{ title: {{ display: true, text: T.trips }}, grid: {{ color: gridColor }} }},
      y1: {{ position: 'right', title: {{ display: true, text: T.km }}, grid: {{ display: false }} }},
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
      label: T.trips,
      data: D.weekdayHour.hour_trips,
      backgroundColor: 'rgba(14,165,233,0.7)',
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      y: {{ title: {{ display: true, text: T.trips }}, grid: {{ color: gridColor }} }},
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
      label: T.l100km_rolling,
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
      y: {{ title: {{ display: true, text: T.l100km }}, grid: {{ color: gridColor }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 15 }} }}
    }}
  }}
}});

// --- Top Journeys Table ---
const tbody = document.getElementById('topTripsBody');
function fmtMin(m) {{
  const h = Math.floor(m / 60), mn = Math.round(m % 60);
  return h > 0 ? `${{h}}h ${{mn}}m` : `${{mn}}m`;
}}
D.longestTrips.forEach(t => {{
  const tr = document.createElement('tr');
  tr.className = 'border-b border-themed row-hover';
  const stops = t.legs - 1;
  let stopsCell;
  if (stops === 0) {{
    stopsCell = '<span class="text-muted">—</span>';
  }} else {{
    const breakStr = t.breaks.map(b => Math.round(b) + 'm').join(' + ');
    stopsCell = `<span title="${{breakStr}}" style="cursor:default">${{stops}} (${{breakStr}})</span>`;
  }}
  tr.innerHTML = `
    <td class="py-2 px-3">${{t.date}}</td>
    <td class="text-right py-2 px-3 font-medium text-heading">${{t.distance}}</td>
    <td class="text-right py-2 px-3">${{fmtMin(t.driving_min)}}</td>
    <td class="text-right py-2 px-3 text-muted">${{fmtMin(t.total_min)}}</td>
    <td class="text-right py-2 px-3">${{stopsCell}}</td>
    <td class="text-right py-2 px-3">${{t.fuel}}</td>
    <td class="text-right py-2 px-3">${{t.avg_fuel}}</td>
    <td class="text-right py-2 px-3">${{t.cost}} ${{D.currency.code}}</td>
    <td class="text-right py-2 px-3">${{t.max_speed ?? '—'}}</td>`;
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
        label: T.odometer_km,
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
        y: {{ title: {{ display: true, text: T.km }}, grid: {{ color: gridColor }} }},
        x: {{ grid: {{ display: false }} }}
      }}
    }}
  }});
}}

// --- Profile Tab ---
if (D.drivingProfile && D.drivingProfile.radar) {{
  // Classification
  document.getElementById('profileLabel').textContent = D.drivingProfile.classification.label;
  document.getElementById('profileDesc').textContent = D.drivingProfile.classification.description;

  // Radar chart
  createChart('radarChart', {{
    type: 'radar',
    data: {{
      labels: D.drivingProfile.radar.labels,
      datasets: [{{
        label: T.your_profile,
        data: D.drivingProfile.radar.values,
        backgroundColor: 'rgba(145,127,101,0.2)',
        borderColor: '#917f65',
        borderWidth: 2,
        pointBackgroundColor: '#917f65',
        pointRadius: 4,
      }}]
    }},
    options: {{
      responsive: true,
      scales: {{
        r: {{
          min: 0, max: 100,
          ticks: {{ stepSize: 20, color: tickColor, backdropColor: 'transparent' }},
          grid: {{ color: gridColor }},
          angleLines: {{ color: gridColor }},
          pointLabels: {{ color: tickColor, font: {{ size: 12 }} }}
        }}
      }},
      plugins: {{ legend: {{ display: false }} }}
    }}
  }});

  // Trip distance distribution
  createChart('tripDistChart', {{
    type: 'bar',
    data: {{
      labels: D.drivingProfile.tripDistribution.labels,
      datasets: [{{
        label: T.trips,
        data: D.drivingProfile.tripDistribution.counts,
        backgroundColor: 'rgba(14,165,233,0.7)',
        borderRadius: 6,
      }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        y: {{ title: {{ display: true, text: T.trips }}, grid: {{ color: gridColor }} }},
        x: {{ grid: {{ display: false }} }}
      }}
    }}
  }});

  // Habits
  document.getElementById('habitNight').textContent = D.drivingProfile.habits.night_pct + '%';
  document.getElementById('habitWeekend').textContent = D.drivingProfile.habits.weekend_pct + '%';
  document.getElementById('habitTripsDay').textContent = D.drivingProfile.habits.trips_per_day;
  document.getElementById('habitPeakHour').textContent = D.drivingProfile.habits.peak_hour;
  document.getElementById('highwayPctVal').textContent = D.drivingProfile.roadType.highway_pct + '%';
  document.getElementById('cityPctVal').textContent = D.drivingProfile.roadType.city_pct + '%';
}}

// --- Engine Recommendation ---
if (D.engineRecommendation && D.engineRecommendation.scores) {{
  const container = document.getElementById('engineScores');
  const typeColors = {{'BEV':'#22c55e','PHEV':'#3b82f6','HEV':'#06b6d4','Petrol':'#f59e0b','Diesel':'#6b7280'}};
  D.engineRecommendation.scores.forEach((eng, i) => {{
    const isTop = i === 0;
    const div = document.createElement('div');
    div.className = 'rounded-xl p-4 text-center' + (isTop ? ' ring-2 ring-lexus-600' : '');
    div.style.background = 'var(--bg-body)';
    const barColor = typeColors[eng.type] || '#917f65';
    div.innerHTML = `
      <div class="text-xs font-semibold text-muted mb-1">${{eng.type}}</div>
      <div class="text-2xl font-bold text-heading">${{eng.score}}</div>
      <div class="w-full rounded-full h-2 mt-2" style="background:var(--border-card)">
        <div class="h-2 rounded-full" style="width:${{eng.score}}%;background:${{barColor}}"></div>
      </div>
      <div class="text-xs text-muted mt-1">${{eng.label}}</div>`;
    container.appendChild(div);
  }});

  // Per-card tab switching
  function switchEngineTab(engType, tabName) {{
    const card = document.getElementById('eng-card-' + engType);
    card.querySelectorAll('[data-eng-panel]').forEach(p => p.style.display = 'none');
    card.querySelector('[data-eng-panel="' + tabName + '"]').style.display = '';
    card.querySelectorAll('.eng-tab-btn').forEach(btn => {{
      const isActive = btn.dataset.engTab === tabName;
      btn.classList.toggle('bg-lexus-600', isActive);
      btn.classList.toggle('text-white', isActive);
      btn.classList.toggle('heat-btn-inactive', !isActive);
    }});
  }}

  const cards = document.getElementById('engineCards');
  const badgeLabels = [T.best_match, T.runner_up, '#3', '#4', '#5'];
  const currentEngine = D.engineRecommendation.current_engine;
  const factorBreakdown = D.engineRecommendation.factor_breakdown || {{}};
  const factorLabelsArr = D.engineRecommendation.factor_labels || [];
  const factorNamesArr = D.engineRecommendation.factor_names || [];
  const globalSavings = D.engineRecommendation.savings;
  const tabWhy = T.eng_tab_why || 'Why';
  const tabAnalysis = T.eng_tab_analysis || 'Analysis';
  const tabCost = T.eng_tab_cost || 'Cost';
  D.engineRecommendation.scores.forEach((eng, i) => {{
    const isTop = i === 0;
    const isCurrent = eng.type === currentEngine;
    const card = document.createElement('div');
    card.className = 'rounded-xl p-6' + (isTop ? ' ring-2 ring-lexus-600' : '');
    card.style.background = 'var(--bg-body)';
    card.id = 'eng-card-' + eng.type;
    if (!isTop) card.style.border = '1px solid var(--border-card)';
    const barColor = typeColors[eng.type] || '#917f65';
    const badgeClass = isTop ? 'bg-lexus-600 text-white' : 'heat-btn-inactive';
    const yourCarBadge = isCurrent ? `<span class="px-2 py-0.5 rounded-full text-xs font-semibold border border-amber-400 text-amber-600">${{T.your_car || 'Your Car'}}</span>` : '';
    const hasRisks = eng.risks && eng.risks.length > 0;
    const riskDot = hasRisks ? '<span class="inline-block w-2 h-2 rounded-full ml-1" style="background:#f59e0b"></span>' : '';

    // --- Why panel content ---
    const reasonsHtml = (eng.reasons || []).map(r => `<li class="flex items-start gap-2"><span class="text-lexus-600 mt-0.5">&#10003;</span><span>${{r}}</span></li>`).join('');
    const whyNotHtml = (eng.why_not || []).map(r => `<li class="flex items-start gap-2"><span class="text-muted mt-0.5">&#10007;</span><span>${{r}}</span></li>`).join('');

    // --- Analysis panel content ---
    let factorHtml = '';
    const fb = factorBreakdown[eng.type];
    if (fb) {{
      const sorted = Object.entries(fb).sort((a,b) => b[1] - a[1]).slice(0, 5);
      const bars = sorted.map(([name, score]) => {{
        const idx = factorNamesArr.indexOf(name);
        const label = idx >= 0 && factorLabelsArr[idx] ? factorLabelsArr[idx] : name.replace(/_/g, ' ');
        return `<div class="flex items-center gap-2 text-xs">
          <span class="w-28 text-muted truncate">${{label}}</span>
          <div class="flex-1 rounded-full h-1.5" style="background:var(--border-card)">
            <div class="h-1.5 rounded-full" style="width:${{score}}%;background:${{barColor}};opacity:0.7"></div>
          </div>
          <span class="text-muted w-8 text-right">${{score}}</span>
        </div>`;
      }}).join('');
      factorHtml = `<div class="text-xs font-semibold text-muted mb-2">${{T.factor_breakdown_label || 'TOP FACTORS'}}</div>
        <div class="space-y-1">${{bars}}</div>`;
    }}

    let risksHtml = '';
    if (hasRisks) {{
      const sevBadge = {{
        high: '<span class="inline-block w-2 h-2 rounded-full mr-1" style="background:#ef4444"></span>',
        medium: '<span class="inline-block w-2 h-2 rounded-full mr-1" style="background:#f59e0b"></span>',
        low: '<span class="inline-block w-2 h-2 rounded-full mr-1" style="background:#3b82f6"></span>'
      }};
      const items = eng.risks.map(r => `<div class="flex items-start gap-2 text-xs">
        <span class="mt-0.5">${{sevBadge[r.severity] || ''}}</span>
        <div><span class="font-semibold text-heading">${{r.label}}</span><br><span class="text-muted">${{r.detail}}</span></div>
      </div>`).join('');
      risksHtml = `<div class="mt-3 pt-3 border-t" style="border-color:var(--border-card)">
        <div class="text-xs font-semibold mb-2" style="color:#f59e0b">${{T.risk_factors_label || 'RISK FACTORS'}}</div>
        <div class="space-y-2">${{items}}</div>
      </div>`;
    }}

    // --- Cost panel content ---
    let costHtml = '';
    if (eng.savings) {{
      const s = eng.savings;
      const sym = (globalSavings && globalSavings.currency_symbol) || '';
      if (s.is_current) {{
        costHtml = `<div class="rounded-lg p-3" style="background:var(--bg-card)">
          <div class="text-xs font-semibold text-muted mb-2">${{T.your_current_cost_label || 'YOUR CURRENT FUEL COST'}}</div>
          <div class="grid grid-cols-2 gap-2 text-sm">
            <div><span class="text-muted text-xs">${{T.monthly_cost_label || 'Monthly'}}</span><br><span class="font-bold text-heading">${{Math.round(s.monthly_cost)}} ${{sym}}</span></div>
            <div><span class="text-muted text-xs">${{T.annual_cost_label || 'Annual'}}</span><br><span class="font-bold text-heading">${{Math.round(s.annual_cost)}} ${{sym}}</span></div>
          </div>
        </div>`;
      }} else {{
        const isGreen = s.pct_saving > 0;
        const color = isGreen ? 'color:#22c55e' : 'color:#ef4444';
        const arrow = isGreen ? '&#8595;' : '&#8593;';
        const sign = isGreen ? '-' : '+';
        const label = isGreen
          ? (T.estimated_savings_label || 'ESTIMATED FUEL SAVINGS')
          : (T.estimated_cost_change_label || 'ESTIMATED FUEL COST');
        costHtml = `<div class="rounded-lg p-3" style="background:var(--bg-card)">
          <div class="text-xs font-semibold text-muted mb-2">${{label}}</div>
          <div class="grid grid-cols-2 gap-2 text-sm">
            <div><span class="text-muted text-xs">${{T.monthly_cost_label || 'Monthly'}}</span><br><span class="font-bold text-heading">${{Math.round(s.monthly_cost)}} ${{sym}}</span></div>
            <div><span class="text-muted text-xs">${{T.annual_cost_label || 'Annual'}}</span><br><span class="font-bold text-heading">${{Math.round(s.annual_cost)}} ${{sym}}</span></div>
          </div>
          <div class="text-xs mt-2 font-semibold" style="${{color}}">${{arrow}} ${{Math.abs(s.pct_saving).toFixed(0)}}% vs ${{T.your_car || 'current'}} &bull; ${{sign}}${{Math.abs(Math.round(s.monthly_saving))}} ${{sym}}/mo</div>
        </div>`;
      }}
    }}

    const et = eng.type;
    card.innerHTML = `
      <div class="flex items-center gap-3 mb-3 flex-wrap">
        <span class="px-3 py-1 rounded-full text-sm font-semibold ${{badgeClass}}">${{badgeLabels[i] || ('#' + (i+1))}}</span>
        ${{yourCarBadge}}
        <span class="font-bold text-heading">${{eng.label}} (${{eng.type}})${{riskDot}}</span>
        <span class="text-sm font-semibold text-muted ml-auto">${{eng.score}}/100</span>
      </div>
      <div class="w-full rounded-full h-1.5 mb-4" style="background:var(--border-card)">
        <div class="h-1.5 rounded-full" style="width:${{eng.score}}%;background:${{barColor}}"></div>
      </div>
      <div class="inline-flex rounded-full p-0.5 gap-0.5 mb-4" style="background:var(--border-card)">
        <button class="eng-tab-btn bg-lexus-600 text-white rounded-full text-xs px-3 py-1 font-semibold cursor-pointer" data-eng-tab="why" onclick="switchEngineTab('${{et}}','why')">${{tabWhy}}</button>
        <button class="eng-tab-btn heat-btn-inactive rounded-full text-xs px-3 py-1 font-semibold cursor-pointer" data-eng-tab="analysis" onclick="switchEngineTab('${{et}}','analysis')">${{tabAnalysis}}</button>
        <button class="eng-tab-btn heat-btn-inactive rounded-full text-xs px-3 py-1 font-semibold cursor-pointer" data-eng-tab="cost" onclick="switchEngineTab('${{et}}','cost')">${{tabCost}}</button>
      </div>
      <div data-eng-panel="why">
        <div class="text-xs font-semibold text-lexus-600 mb-1">${{T.why_yes_label || 'WHY YES'}}</div>
        <ul class="space-y-1 text-sm text-heading mb-3">${{reasonsHtml}}</ul>
        <div class="text-xs font-semibold text-muted mb-1">${{T.why_not_label || 'WHY NOT'}}</div>
        <ul class="space-y-1 text-sm text-muted">${{whyNotHtml}}</ul>
      </div>
      <div data-eng-panel="analysis" style="display:none">
        ${{factorHtml}}
        ${{risksHtml}}
      </div>
      <div data-eng-panel="cost" style="display:none">
        ${{costHtml}}
      </div>`;
    cards.appendChild(card);
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
  const valid = ['overview','fuel-ev','driving','trips','profile'];
  switchTab(valid.includes(saved) ? saved : 'overview');
}})();
</script>
</body>
</html>"""
    return html


def build_dashboard_for_vehicle(conn: sqlite3.Connection, vehicle: dict,
                                country_code: str = "PL",
                                currency_code: str | None = None,
                                lang: str = "en") -> Path:
    """Build a dashboard HTML for a single vehicle. Returns the output path."""
    vin = vehicle["vin"]
    alias = vehicle["alias"]
    brand = vehicle["brand"]
    fuel_type = vehicle.get("fuel_type", "gasoline")
    label = f"{alias} ({brand})" if brand else alias

    country_info = get_country_info(country_code)
    tz_name = country_info["tz"]
    native_currency = country_info["currency"]
    display_currency = currency_code or native_currency
    currency_symbol = country_info["symbol"]

    # If display currency differs, look up symbol from any matching country
    if display_currency != native_currency:
        for info in COUNTRY_INFO.values():
            if info["currency"] == display_currency:
                currency_symbol = info["symbol"]
                break

    # Load cache and compute exchange rate
    cache = load_cache()
    exchange_rate = get_exchange_rate(native_currency, display_currency, cache)

    # Build a price function that returns price in display currency
    def price_fn(month: str) -> float:
        native_price = get_fuel_price(country_code, month, fuel_type, conn=conn, cache=cache)
        return round(native_price * exchange_rate, 2)

    print(f"\n{'='*60}")
    print(f"Building dashboard for: {label}")
    print(f"  Country: {country_code}, Fuel: {fuel_type}, Currency: {display_currency}"
          + (f" (rate: {exchange_rate:.4f})" if display_currency != native_currency else ""))
    print(f"{'='*60}")

    print("Loading trips...")
    trips = load_trips(conn, vin, tz_name)
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

    t = get_translations(lang)

    print("Computing aggregations...")
    kpis = compute_kpis(trips, price_fn=price_fn)
    monthly = compute_monthly(trips, price_fn=price_fn)
    wh = compute_weekday_hour(trips, t)
    sd = compute_score_distribution(trips)
    lt = top_journeys(trips, price_fn=price_fn)
    tc = compute_trip_categories(trips, t)
    sea = compute_seasonal(trips, t)
    dm = compute_driving_modes(trips, t)
    sa = compute_speed_analytics(trips)
    hc = compute_highway_city_split(trips)
    nd = compute_night_driving(trips)
    idle = compute_idle_analysis(trips)
    profile = compute_driving_profile(trips, t)
    engine_rec = compute_engine_recommendation(
        trips, profile, t, fuel_type=fuel_type,
        engine_type=vehicle.get("engine_type"),
        seasonal=sea, night_driving=nd, kpis=kpis,
        monthly=monthly, journeys=lt, idle=idle,
        price_fn=price_fn, currency_symbol=currency_symbol,
    )

    print("Building HTML...")
    html = build_html(kpis, monthly, wh, sd, heatmap_layers, lt, tc, sea, trips,
                      dm, sa, hc, nd, idle, service_history, odometer_data,
                      driving_profile=profile, engine_recommendation=engine_rec,
                      vehicle=vehicle,
                      currency_code=display_currency, currency_symbol=currency_symbol,
                      country_code=country_code, fuel_type=fuel_type,
                      t=t, lang=lang)

    # Save cache after all lookups
    save_cache(cache)

    # Sanitize alias for filename
    safe_alias = "".join(c if c.isalnum() or c in "-_ " else "" for c in alias).strip().replace(" ", "_")
    output_path = Path(__file__).parent / f"dashboard_{safe_alias}.html"
    output_path.write_text(html)
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Saved to {output_path} ({size_mb:.1f} MB)")
    return output_path


def load_vehicle_fuel_type(conn: sqlite3.Connection, vin: str) -> str:
    """Read the fuel_type column from vehicles table."""
    try:
        row = conn.execute("SELECT fuel_type FROM vehicles WHERE vin = ?", (vin,)).fetchone()
        if row and row[0]:
            return row[0]
    except sqlite3.OperationalError:
        pass
    return "gasoline"


def main():
    parser = argparse.ArgumentParser(description="Build trip analytics dashboard(s).")
    parser.add_argument("--country", default="PL",
                        help="ISO country code for fuel prices (default: PL)")
    parser.add_argument("--currency", default=None,
                        help="Display currency code (default: country's native currency)")
    parser.add_argument("--lang", default="en",
                        help="Dashboard language: en, pl (default: en)")
    args = parser.parse_args()

    country_code = args.country.upper()
    lang = args.lang.lower()
    # Validate country
    get_country_info(country_code)

    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        print("Run backfill.py first.")
        raise SystemExit(1)

    conn = sqlite3.connect(DB_PATH)

    # Ensure fuel_prices table exists (for users who haven't re-run backfill yet)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fuel_prices (
            country   TEXT NOT NULL,
            month     TEXT NOT NULL,
            fuel_type TEXT NOT NULL DEFAULT 'gasoline',
            currency  TEXT NOT NULL,
            price     REAL NOT NULL,
            source    TEXT DEFAULT 'scraped',
            PRIMARY KEY (country, month, fuel_type)
        )
    """)
    from fuel_config import seed_pl_prices
    seed_pl_prices(conn)

    vehicles = load_all_vehicles(conn)
    if not vehicles:
        # Fall back to distinct VINs from trips table
        vins = conn.execute("SELECT DISTINCT vin FROM trips").fetchall()
        vehicles = [{"vin": r[0], "alias": r[0][:8], "brand": ""} for r in vins if r[0]]
    if not vehicles:
        print("No vehicles found in database. Run backfill.py first.")
        raise SystemExit(1)

    # Enrich vehicles with fuel_type from DB
    for v in vehicles:
        v["fuel_type"] = load_vehicle_fuel_type(conn, v["vin"])

    print(f"Found {len(vehicles)} vehicle(s):")
    for v in vehicles:
        label = f"{v['alias']} ({v['brand']})" if v['brand'] else v['alias']
        print(f"  - {label} [{v['fuel_type']}]")

    outputs = []
    for vehicle in vehicles:
        path = build_dashboard_for_vehicle(conn, vehicle,
                                           country_code=country_code,
                                           currency_code=args.currency,
                                           lang=lang)
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
