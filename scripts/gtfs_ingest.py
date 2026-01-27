"""Download a GTFS zip and build a SQLite schedule DB.

Usage:
  python scripts/gtfs_ingest.py

Configuration via env vars:
  - GTFS_URL (REQUIRED): direct URL to a GTFS .zip
  - GTFS_DB_PATH (optional): default 'data/gtfs.sqlite'
  - GTFS_TIMEOUT (optional): default 60 (seconds)

Why this exists:
  Your agent needs a complete schedule source for all stops (by stop_code),
  not only the landmark tables on the website.
"""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import requests


GTFS_URL = os.environ.get("GTFS_URL", "").strip()
DB_PATH = os.environ.get("GTFS_DB_PATH", "data/gtfs.sqlite")
TIMEOUT = int(os.environ.get("GTFS_TIMEOUT", "60"))


def die(msg: str) -> None:
    print("❌", msg)
    raise SystemExit(1)


def time_to_secs(t: str) -> int | None:
    """Convert HH:MM:SS (or HH:MM) to seconds.

    Supports HH >= 24 (GTFS after-midnight times).
    """
    if not t:
        return None
    s = t.strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) == 2:
        hh, mm = parts
        ss = "0"
    elif len(parts) == 3:
        hh, mm, ss = parts
    else:
        return None
    try:
        hh_i = int(hh)
        mm_i = int(mm)
        ss_i = int(ss)
        return hh_i * 3600 + mm_i * 60 + ss_i
    except Exception:
        return None


def fetch_gtfs_zip(url: str) -> bytes:
    """
    Downloads GTFS zip. Some servers return 406 unless we look like a browser.
    Retries a few times with different headers.
    """
    header_sets = [
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://go-rts.com/",
            "Connection": "keep-alive",
        },
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Safari/605.1.15"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://go-rts.com/rts-data/",
        },
    ]

    last_err: Exception | None = None

    for headers in header_sets:
        for attempt in range(1, 4):
            try:
                r = requests.get(
                    url,
                    headers=headers,
                    allow_redirects=True,
                    timeout=(15, 180),  # (connect timeout, read timeout)
                    stream=True,
                )

                # If server rejects this header set, try the next one
                if r.status_code == 406:
                    last_err = requests.HTTPError(f"406 Not Acceptable for {url}")
                    break

                r.raise_for_status()
                return r.content

            except Exception as e:
                last_err = e
                time.sleep(1.5 * attempt)

    raise last_err or RuntimeError("Failed to download GTFS zip")


def read_csv_from_zip(z: zipfile.ZipFile, name: str) -> list[dict]:
    try:
        raw = z.read(name)
    except KeyError:
        return []
    # UTF-8 is typical; if agency uses BOM, this handles it.
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def build_db(gtfs_bytes: bytes, db_path: str) -> None:
    ensure_parent(db_path)
    if os.path.exists(db_path):
        os.remove(db_path)

    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()

        cur.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE stops (
                stop_id TEXT PRIMARY KEY,
                stop_code TEXT,
                stop_name TEXT,
                stop_lat REAL,
                stop_lon REAL
            );
            CREATE INDEX idx_stops_code ON stops(stop_code);

            CREATE TABLE routes (
                route_id TEXT PRIMARY KEY,
                route_short_name TEXT,
                route_long_name TEXT
            );

            CREATE TABLE trips (
                trip_id TEXT PRIMARY KEY,
                route_id TEXT,
                service_id TEXT,
                trip_headsign TEXT,
                direction_id INTEGER,
                FOREIGN KEY(route_id) REFERENCES routes(route_id)
            );

            CREATE TABLE stop_times (
                trip_id TEXT,
                stop_id TEXT,
                stop_sequence INTEGER,
                arrival_time TEXT,
                departure_time TEXT,
                dep_secs INTEGER,
                PRIMARY KEY(trip_id, stop_sequence)
            );

            CREATE TABLE calendar (
                service_id TEXT PRIMARY KEY,
                monday INTEGER,
                tuesday INTEGER,
                wednesday INTEGER,
                thursday INTEGER,
                friday INTEGER,
                saturday INTEGER,
                sunday INTEGER,
                start_date TEXT,
                end_date TEXT
            );

            CREATE TABLE calendar_dates (
                service_id TEXT,
                date TEXT,
                exception_type INTEGER
            );

            CREATE INDEX idx_routes_short ON routes(route_short_name);
            CREATE INDEX idx_trips_route ON trips(route_id);
            CREATE INDEX idx_trips_service ON trips(service_id);
            CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
            CREATE INDEX idx_stop_times_dep ON stop_times(dep_secs);
            """
        )

        z = zipfile.ZipFile(io.BytesIO(gtfs_bytes))

        stops = read_csv_from_zip(z, "stops.txt")
        routes = read_csv_from_zip(z, "routes.txt")
        trips = read_csv_from_zip(z, "trips.txt")
        stop_times = read_csv_from_zip(z, "stop_times.txt")
        calendar = read_csv_from_zip(z, "calendar.txt")
        cal_dates = read_csv_from_zip(z, "calendar_dates.txt")

        if not stops or not routes or not trips or not stop_times:
            die("GTFS zip missing required files (stops/routes/trips/stop_times)")

        print(
            f"📥 Parsed: stops={len(stops)} routes={len(routes)} trips={len(trips)} stop_times={len(stop_times)}"
        )

        # Insert stops (IMPORTANT: store stop_code for 4-digit sign numbers)
        cur.executemany(
            "INSERT INTO stops(stop_id, stop_code, stop_name, stop_lat, stop_lon) VALUES(?,?,?,?,?)",
            [
                (
                    r.get("stop_id"),
                    r.get("stop_code") or "",
                    r.get("stop_name"),
                    float(r["stop_lat"]) if r.get("stop_lat") else None,
                    float(r["stop_lon"]) if r.get("stop_lon") else None,
                )
                for r in stops
                if r.get("stop_id")
            ],
        )

        # Insert routes
        cur.executemany(
            "INSERT INTO routes(route_id, route_short_name, route_long_name) VALUES(?,?,?)",
            [
                (
                    r.get("route_id"),
                    r.get("route_short_name"),
                    r.get("route_long_name"),
                )
                for r in routes
                if r.get("route_id")
            ],
        )

        # Insert trips
        cur.executemany(
            "INSERT INTO trips(trip_id, route_id, service_id, trip_headsign, direction_id) VALUES(?,?,?,?,?)",
            [
                (
                    r.get("trip_id"),
                    r.get("route_id"),
                    r.get("service_id"),
                    r.get("trip_headsign"),
                    int(r["direction_id"]) if r.get("direction_id") not in (None, "") else None,
                )
                for r in trips
                if r.get("trip_id")
            ],
        )

        # Insert stop_times (compute dep_secs)
        rows = []
        for r in stop_times:
            trip_id = r.get("trip_id")
            stop_id = r.get("stop_id")
            seq = r.get("stop_sequence")
            if not trip_id or not stop_id or not seq:
                continue
            dep = r.get("departure_time") or r.get("arrival_time")
            dep_secs = time_to_secs(dep or "")
            rows.append(
                (
                    trip_id,
                    stop_id,
                    int(seq),
                    r.get("arrival_time"),
                    r.get("departure_time"),
                    dep_secs,
                )
            )
        cur.executemany(
            "INSERT INTO stop_times(trip_id, stop_id, stop_sequence, arrival_time, departure_time, dep_secs) VALUES(?,?,?,?,?,?)",
            rows,
        )

        # calendar / calendar_dates are optional but highly recommended
        if calendar:
            cur.executemany(
                """
                INSERT INTO calendar(
                    service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        r.get("service_id"),
                        int(r.get("monday") or 0),
                        int(r.get("tuesday") or 0),
                        int(r.get("wednesday") or 0),
                        int(r.get("thursday") or 0),
                        int(r.get("friday") or 0),
                        int(r.get("saturday") or 0),
                        int(r.get("sunday") or 0),
                        r.get("start_date"),
                        r.get("end_date"),
                    )
                    for r in calendar
                    if r.get("service_id")
                ],
            )

        if cal_dates:
            cur.executemany(
                "INSERT INTO calendar_dates(service_id, date, exception_type) VALUES(?,?,?)",
                [
                    (
                        r.get("service_id"),
                        r.get("date"),
                        int(r.get("exception_type") or 0),
                    )
                    for r in cal_dates
                    if r.get("service_id") and r.get("date")
                ],
            )

        con.commit()

        # quick sanity output
        n = cur.execute("SELECT COUNT(*) FROM stop_times").fetchone()[0]
        s = cur.execute("SELECT COUNT(*) FROM stops").fetchone()[0]
        print(f"✅ SQLite GTFS DB created: {db_path} (stops={s}, stop_times={n})")

    finally:
        con.close()


def main() -> None:
    if not GTFS_URL:
        die("GTFS_URL env var is required. Set it in Render to the direct .zip link.")
    print(f"⬇️  Downloading GTFS: {GTFS_URL}")
    gtfs_bytes = fetch_gtfs_zip(GTFS_URL)
    build_db(gtfs_bytes, DB_PATH)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
