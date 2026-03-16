"""Backfill historical trip data from Lexus Link+ API into SQLite."""

import asyncio
import os
import sqlite3
import sys
from datetime import date, timedelta
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


def upsert_trips(conn: sqlite3.Connection, trips: list, vin: str) -> tuple[int, int]:
    """Insert or update trips. Returns (inserted, updated) counts."""
    existing = {
        row[0]
        for row in conn.execute("SELECT trip_start_time FROM trips").fetchall()
    }

    inserted = 0
    updated = 0

    for t in trips:
        if not t.start_time:
            continue

        start_loc = t.locations.start if t.locations else None
        end_loc = t.locations.end if t.locations else None
        key = t.start_time.isoformat()

        conn.execute(
            """INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(trip_start_time) DO UPDATE SET
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
                   score_advice=excluded.score_advice""",
            (
                key,
                t.end_time.isoformat() if t.end_time else None,
                t.duration.total_seconds() if t.duration else None,
                t.distance,
                t.ev_distance,
                t.fuel_consumed,
                t.average_fuel_consumed,
                start_loc.lat if start_loc else None,
                start_loc.lon if start_loc else None,
                end_loc.lat if end_loc else None,
                end_loc.lon if end_loc else None,
                t.score,
                t.score_acceleration,
                t.score_braking,
                t.score_constant_speed,
                t.score_advice,
                vin,
            ),
        )

        if key in existing:
            updated += 1
        else:
            inserted += 1

        # Upsert waypoints: delete old, insert fresh
        if t.route:
            conn.execute(
                "DELETE FROM waypoints WHERE trip_start_time = ?", (key,)
            )
            conn.executemany(
                "INSERT INTO waypoints VALUES (?,?,?,?)",
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
        from datetime import datetime as dt
        return dt.fromisoformat(row[0]).date()
    return FULL_BACKFILL_START


async def fetch_all_trips(client: MyT, vin: str, start_date: date) -> list:
    """Fetch all trips from start_date to today with windowing."""
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

    return all_trips


async def main():
    full = "--full" in sys.argv

    username = os.environ.get("LEXUS_USERNAME")
    password = os.environ.get("LEXUS_PASSWORD")
    vin = os.environ.get("LEXUS_VIN")

    if not username or not password or not vin:
        print("Set LEXUS_USERNAME, LEXUS_PASSWORD, and LEXUS_VIN environment variables.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if full:
        start_date = FULL_BACKFILL_START
        print("Full backfill requested.")
    else:
        start_date = get_fetch_start(conn)
        print(f"Incremental mode: fetching from {start_date}")

    print(f"Logging in as {username} (brand=L)...")
    client = MyT(username=username, password=password, brand="L")
    await client.login()
    print("Login successful!")

    print(f"\nFetching trips from {start_date} to {BACKFILL_END}...")
    trips = await fetch_all_trips(client, vin, start_date)

    if not trips:
        print("No new trips found.")
        conn.close()
        sys.exit(0)

    print(f"\nTotal trips fetched: {len(trips)}")

    before = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    inserted, updated = upsert_trips(conn, trips, vin)
    after = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    waypoints = conn.execute("SELECT COUNT(*) FROM waypoints").fetchone()[0]

    conn.close()

    print(f"\nDatabase: {DB_PATH}")
    print(f"  Before: {before} trips")
    print(f"  Inserted: {inserted}, Updated: {updated}")
    print(f"  Total: {after} trips, {waypoints:,} waypoints")
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
