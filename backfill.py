"""Backfill historical trip data from Lexus Link+ API into SQLite."""

import asyncio
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from pytoyoda.client import MyT

DB_PATH = Path(__file__).parent / "trips.db"
# Full backfill goes back to 2024; incremental starts from last known trip
FULL_BACKFILL_START = date(2024, 1, 1)
BACKFILL_END = date.today()
WINDOW_DAYS = 30


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trips (
            trip_start_time TEXT PRIMARY KEY,
            trip_end_time   TEXT,
            duration_sec    REAL,
            distance_km     REAL,
            ev_distance_km  REAL,
            fuel_consumed_l REAL,
            avg_fuel_l100km REAL,
            start_lat       REAL,
            start_lng       REAL,
            end_lat         REAL,
            end_lng         REAL,
            score           INTEGER,
            score_accel     INTEGER,
            score_braking   INTEGER,
            score_constant  INTEGER,
            score_advice    INTEGER,
            vin             TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS waypoints (
            trip_start_time TEXT    NOT NULL,
            idx             INTEGER NOT NULL,
            lat             REAL    NOT NULL,
            lng             REAL    NOT NULL,
            PRIMARY KEY (trip_start_time, idx),
            FOREIGN KEY (trip_start_time) REFERENCES trips(trip_start_time)
        );

        CREATE INDEX IF NOT EXISTS idx_waypoints_trip
            ON waypoints(trip_start_time);
    """)


def migrate_db(conn: sqlite3.Connection) -> None:
    """Add new columns and tables for enriched data (idempotent)."""
    trip_columns = [
        ("ev_duration_sec", "REAL"),
        ("eco_time_sec", "REAL"),
        ("eco_distance_m", "REAL"),
        ("power_time_sec", "REAL"),
        ("power_distance_m", "REAL"),
        ("charge_time_sec", "REAL"),
        ("charge_distance_m", "REAL"),
        ("max_speed_kmh", "REAL"),
        ("avg_speed_kmh", "REAL"),
        ("highway_distance_m", "REAL"),
        ("highway_duration_sec", "REAL"),
        ("idle_duration_sec", "REAL"),
        ("night_trip", "INTEGER"),
        ("overspeed_distance_m", "REAL"),
        ("overspeed_duration_sec", "REAL"),
        ("countries", "TEXT"),
        ("trip_category", "INTEGER"),
    ]
    waypoint_columns = [
        ("overspeed", "INTEGER"),
        ("highway", "INTEGER"),
        ("is_ev", "INTEGER"),
        ("mode", "INTEGER"),
    ]
    for col, typ in trip_columns:
        try:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # column already exists
    for col, typ in waypoint_columns:
        try:
            conn.execute(f"ALTER TABLE waypoints ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS service_history (
            service_history_id TEXT PRIMARY KEY,
            vin                TEXT NOT NULL,
            service_date       TEXT,
            service_category   TEXT,
            service_provider   TEXT,
            odometer           INTEGER,
            operations_performed TEXT,
            notes              TEXT
        );

        CREATE TABLE IF NOT EXISTS vehicles (
            vin   TEXT PRIMARY KEY,
            alias TEXT,
            brand TEXT
        );

        CREATE TABLE IF NOT EXISTS telemetry_snapshots (
            captured_at     TEXT PRIMARY KEY,
            vin             TEXT NOT NULL,
            odometer        REAL,
            fuel_level      INTEGER,
            battery_level   REAL,
            fuel_range      REAL,
            battery_range   REAL,
            charging_status TEXT
        );
    """)
    conn.commit()


def upsert_trips(conn: sqlite3.Connection, trips: list, vin: str) -> tuple[int, int]:
    """Insert or update trips with enriched data. Returns (inserted, updated) counts."""
    existing = {
        row[0]
        for row in conn.execute("SELECT trip_start_time FROM trips").fetchall()
    }

    inserted = 0
    updated = 0

    for t in trips:
        if not t.start_time:
            continue

        raw = t._trip  # _TripModel with full API data
        summary = raw.summary if raw else None
        hdc = raw.hdc if raw else None

        start_loc = t.locations.start if t.locations else None
        end_loc = t.locations.end if t.locations else None
        key = t.start_time.isoformat()

        conn.execute(
            """INSERT INTO trips (
                trip_start_time, trip_end_time, duration_sec, distance_km, ev_distance_km,
                fuel_consumed_l, avg_fuel_l100km, start_lat, start_lng, end_lat, end_lng,
                score, score_accel, score_braking, score_constant, score_advice, vin,
                ev_duration_sec, eco_time_sec, eco_distance_m, power_time_sec, power_distance_m,
                charge_time_sec, charge_distance_m, max_speed_kmh, avg_speed_kmh,
                highway_distance_m, highway_duration_sec, idle_duration_sec, night_trip,
                overspeed_distance_m, overspeed_duration_sec, countries, trip_category
            ) VALUES (
                :trip_start_time, :trip_end_time, :duration_sec, :distance_km, :ev_distance_km,
                :fuel_consumed_l, :avg_fuel_l100km, :start_lat, :start_lng, :end_lat, :end_lng,
                :score, :score_accel, :score_braking, :score_constant, :score_advice, :vin,
                :ev_duration_sec, :eco_time_sec, :eco_distance_m, :power_time_sec, :power_distance_m,
                :charge_time_sec, :charge_distance_m, :max_speed_kmh, :avg_speed_kmh,
                :highway_distance_m, :highway_duration_sec, :idle_duration_sec, :night_trip,
                :overspeed_distance_m, :overspeed_duration_sec, :countries, :trip_category
            ) ON CONFLICT(trip_start_time) DO UPDATE SET
                trip_end_time=excluded.trip_end_time,
                duration_sec=excluded.duration_sec,
                distance_km=excluded.distance_km,
                ev_distance_km=excluded.ev_distance_km,
                fuel_consumed_l=excluded.fuel_consumed_l,
                avg_fuel_l100km=excluded.avg_fuel_l100km,
                start_lat=excluded.start_lat,
                start_lng=excluded.start_lng,
                end_lat=excluded.end_lat,
                end_lng=excluded.end_lng,
                score=excluded.score,
                score_accel=excluded.score_accel,
                score_braking=excluded.score_braking,
                score_constant=excluded.score_constant,
                score_advice=excluded.score_advice,
                ev_duration_sec=excluded.ev_duration_sec,
                eco_time_sec=excluded.eco_time_sec,
                eco_distance_m=excluded.eco_distance_m,
                power_time_sec=excluded.power_time_sec,
                power_distance_m=excluded.power_distance_m,
                charge_time_sec=excluded.charge_time_sec,
                charge_distance_m=excluded.charge_distance_m,
                max_speed_kmh=excluded.max_speed_kmh,
                avg_speed_kmh=excluded.avg_speed_kmh,
                highway_distance_m=excluded.highway_distance_m,
                highway_duration_sec=excluded.highway_duration_sec,
                idle_duration_sec=excluded.idle_duration_sec,
                night_trip=excluded.night_trip,
                overspeed_distance_m=excluded.overspeed_distance_m,
                overspeed_duration_sec=excluded.overspeed_duration_sec,
                countries=excluded.countries,
                trip_category=excluded.trip_category""",
            {
                "trip_start_time": key,
                "trip_end_time": t.end_time.isoformat() if t.end_time else None,
                "duration_sec": t.duration.total_seconds() if t.duration else None,
                "distance_km": t.distance,
                "ev_distance_km": t.ev_distance,
                "fuel_consumed_l": t.fuel_consumed,
                "avg_fuel_l100km": t.average_fuel_consumed,
                "start_lat": start_loc.lat if start_loc else None,
                "start_lng": start_loc.lon if start_loc else None,
                "end_lat": end_loc.lat if end_loc else None,
                "end_lng": end_loc.lon if end_loc else None,
                "score": t.score,
                "score_accel": t.score_acceleration,
                "score_braking": t.score_braking,
                "score_constant": t.score_constant_speed,
                "score_advice": t.score_advice,
                "vin": vin,
                "ev_duration_sec": hdc.ev_time if hdc else None,
                "eco_time_sec": hdc.eco_time if hdc else None,
                "eco_distance_m": hdc.eco_dist if hdc else None,
                "power_time_sec": hdc.power_time if hdc else None,
                "power_distance_m": hdc.power_dist if hdc else None,
                "charge_time_sec": hdc.charge_time if hdc else None,
                "charge_distance_m": hdc.charge_dist if hdc else None,
                "max_speed_kmh": summary.max_speed if summary else None,
                "avg_speed_kmh": summary.average_speed if summary else None,
                "highway_distance_m": summary.length_highway if summary else None,
                "highway_duration_sec": summary.duration_highway if summary else None,
                "idle_duration_sec": summary.duration_idle if summary else None,
                "night_trip": int(summary.night_trip) if summary and summary.night_trip is not None else None,
                "overspeed_distance_m": summary.length_overspeed if summary else None,
                "overspeed_duration_sec": summary.duration_overspeed if summary else None,
                "countries": json.dumps(summary.countries) if summary and summary.countries else None,
                "trip_category": raw.category if raw else None,
            },
        )

        if key in existing:
            updated += 1
        else:
            inserted += 1

        # Upsert waypoints: prefer raw route (has enriched metadata)
        raw_route = raw.route if raw else None
        if raw_route:
            conn.execute(
                "DELETE FROM waypoints WHERE trip_start_time = ?", (key,)
            )
            conn.executemany(
                "INSERT INTO waypoints (trip_start_time, idx, lat, lng, overspeed, highway, is_ev, mode) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        key, idx, p.lat, p.lon,
                        int(p.overspeed) if p.overspeed is not None else None,
                        int(p.highway) if p.highway is not None else None,
                        int(p.is_ev) if p.is_ev is not None else None,
                        p.mode,
                    )
                    for idx, p in enumerate(raw_route)
                    if p and p.lat is not None and p.lon is not None
                ],
            )
        elif t.route:
            conn.execute(
                "DELETE FROM waypoints WHERE trip_start_time = ?", (key,)
            )
            conn.executemany(
                "INSERT INTO waypoints (trip_start_time, idx, lat, lng) VALUES (?,?,?,?)",
                [
                    (key, idx, p.lat, p.lon)
                    for idx, p in enumerate(t.route)
                    if p
                ],
            )

    conn.commit()
    return inserted, updated


def get_fetch_start(conn: sqlite3.Connection) -> date:
    """Start from the last known trip date (with 1-day overlap for safety)."""
    row = conn.execute(
        "SELECT MAX(trip_start_time) FROM trips"
    ).fetchone()
    if row and row[0]:
        return datetime.fromisoformat(row[0]).date()
    return FULL_BACKFILL_START


async def fetch_all_trips(client: MyT, vin: str, start_date: date) -> tuple[list, object]:
    """Fetch all trips from start_date to today with windowing. Returns (trips, vehicle)."""
    vehicles = await client.get_vehicles()
    vehicle = next((v for v in vehicles if v.vin == vin), None)

    if vehicle is None:
        print(f"Vehicle {vin} not found. Available VINs:")
        for v in vehicles:
            print(f"  - {v.vin} ({v.alias})")
        sys.exit(1)

    print(f"Found vehicle: {vehicle.vin} ({vehicle.alias})")

    all_trips = []
    window_start = start_date

    while window_start < BACKFILL_END:
        window_end = min(window_start + timedelta(days=WINDOW_DAYS), BACKFILL_END)
        print(f"  Fetching {window_start} -> {window_end}...")

        trips = await vehicle.get_trips(
            from_date=window_start,
            to_date=window_end,
            full_route=True,
        )

        if trips:
            all_trips.extend(trips)
            print(f"    Got {len(trips)} trips (total: {len(all_trips)})")

        window_start = window_end + timedelta(days=1)

    return all_trips, vehicle


async def fetch_and_store_service_history(vehicle, conn: sqlite3.Connection, vin: str) -> int:
    """Fetch service history from vehicle and store in database."""
    try:
        await vehicle.update()
    except Exception as e:
        print(f"  Warning: vehicle.update() failed: {e}")
        return 0

    history = vehicle.service_history
    if not history:
        print("  No service history available.")
        return 0

    count = 0
    for svc in history:
        # Get service_history_id from wrapper or underlying model
        sid = getattr(svc, "service_history_id", None)
        if sid is None:
            raw_svc = getattr(svc, "_service_history", None)
            if raw_svc:
                sid = getattr(raw_svc, "service_history_id", None)
        if not sid:
            continue

        conn.execute(
            """INSERT OR REPLACE INTO service_history
               (service_history_id, vin, service_date, service_category,
                service_provider, odometer, operations_performed, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(sid),
                vin,
                str(svc.service_date) if svc.service_date else None,
                svc.service_category,
                svc.service_provider,
                svc.odometer,
                json.dumps(svc.operations_performed) if svc.operations_performed else None,
                str(svc.notes) if svc.notes else None,
            ),
        )
        count += 1
    conn.commit()
    return count


async def store_telemetry_snapshot(vehicle, conn: sqlite3.Connection, vin: str) -> None:
    """Store current vehicle dashboard readings as a telemetry snapshot."""
    dash = vehicle.dashboard
    if not dash:
        print("  No dashboard data available.")
        return

    now = datetime.now().isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO telemetry_snapshots
           (captured_at, vin, odometer, fuel_level, battery_level,
            fuel_range, battery_range, charging_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            now, vin,
            dash.odometer, dash.fuel_level, dash.battery_level,
            dash.fuel_range, dash.battery_range, dash.charging_status,
        ),
    )
    conn.commit()
    print(f"  Telemetry snapshot saved (odometer: {dash.odometer}, fuel: {dash.fuel_level}%)")


async def main():
    full = "--full" in sys.argv

    username = os.environ.get("CAR_USERNAME")
    password = os.environ.get("CAR_PASSWORD")
    vin = os.environ.get("CAR_VIN")
    brand = os.environ.get("CAR_BRAND", "L").upper()

    if brand not in ("L", "T"):
        print(f"Invalid CAR_BRAND '{brand}'. Use 'L' (Lexus) or 'T' (Toyota).")
        sys.exit(1)

    if not username or not password or not vin:
        print("Set CAR_USERNAME, CAR_PASSWORD, and CAR_VIN environment variables.")
        print("  Optional: CAR_BRAND=L (Lexus, default) or CAR_BRAND=T (Toyota)")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    migrate_db(conn)

    if full:
        start_date = FULL_BACKFILL_START
        print("Full backfill requested.")
    else:
        start_date = get_fetch_start(conn)
        print(f"Incremental mode: fetching from {start_date}")

    brand_label = "Lexus" if brand == "L" else "Toyota"
    print(f"Logging in as {username} (brand={brand}, {brand_label})...")
    client = MyT(username=username, password=password, brand=brand)
    await client.login()
    print("Login successful!")

    print(f"\nFetching trips from {start_date} to {BACKFILL_END}...")
    trips, vehicle = await fetch_all_trips(client, vin, start_date)

    # Store vehicle info
    conn.execute(
        "INSERT OR REPLACE INTO vehicles (vin, alias, brand) VALUES (?, ?, ?)",
        (vin, vehicle.alias, brand_label),
    )
    conn.commit()

    if not trips:
        print("No new trips found.")
    else:
        print(f"\nTotal trips fetched: {len(trips)}")

        before = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        inserted, updated = upsert_trips(conn, trips, vin)
        after = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        waypoints = conn.execute("SELECT COUNT(*) FROM waypoints").fetchone()[0]

        print(f"\nDatabase: {DB_PATH}")
        print(f"  Before: {before} trips")
        print(f"  Inserted: {inserted}, Updated: {updated}")
        print(f"  Total: {after} trips, {waypoints:,} waypoints")

    print("\nFetching service history & telemetry...")
    svc_count = await fetch_and_store_service_history(vehicle, conn, vin)
    print(f"  Service records: {svc_count}")
    await store_telemetry_snapshot(vehicle, conn, vin)

    conn.close()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
